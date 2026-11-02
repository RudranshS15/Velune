from pathlib import Path
import re

# 1) Missing expiry is metadata, not a fatal error.
model = Path("innertube/src/main/kotlin/com/nikhil/yt/innertube/models/response/PlayerResponse.kt")
text = model.read_text(encoding="utf-8")
old = "val expiresInSeconds: Int,"
assert text.count(old) == 1, f"model pattern matches: {text.count(old)}"
model.write_text(text.replace(old, "val expiresInSeconds: Int = 0,"), encoding="utf-8")

player = Path("app/src/main/kotlin/com/nikhil/yt/utils/YTPlayerUtils.kt")
text = player.read_text(encoding="utf-8")

pattern = re.compile(
    r"([ \t]*)if \(streamExpiresInSeconds == null\) \{\n"
    r"[ \t]*streamPlayerResponse = null\n"
    r"[ \t]*continue\n"
    r"[ \t]*\}"
)

def replacement(match):
    indent = match.group(1)
    return (
        f"{indent}if (streamExpiresInSeconds == null || streamExpiresInSeconds <= 0) {{\n"
        f"{indent}    // YouTube may omit the expiry; derive it from the signed URL or default.\n"
        f"{indent}    streamExpiresInSeconds = selectedUrl.toHttpUrlOrNull()\n"
        f'{indent}        ?.queryParameter("expire")?.toLongOrNull()\n'
        f"{indent}        ?.let {{ (it - System.currentTimeMillis() / 1000L).toInt() }}\n"
        f"{indent}        ?.takeIf {{ it > 0 }} ?: 3600\n"
        f"{indent}}}"
    )

text, count = pattern.subn(replacement, text)
assert count == 1, f"player expiry pattern matches: {count}"

# 2) Re-extract the stream URL with NewPipe instead of trusting a direct format.url.
old_url = "var url = NewPipeUtils.getStreamUrl(format, videoId, client)"
assert text.count(old_url) == 1, f"stream url pattern matches: {text.count(old_url)}"
fresh = '''NewPipeUtils.getFreshStreamUrl(videoId, format.itag).getOrNull()?.let { freshUrl ->
    Timber.tag(logTag).i("Using fresh NewPipe stream URL for itag ${format.itag}")
    return freshUrl
}
var url = NewPipeUtils.getStreamUrl(format, videoId, client)'''
text = text.replace(old_url, fresh)
player.write_text(text, encoding="utf-8")

# 3) Add the NewPipe fresh-URL helper.
newpipe = Path("innertube/src/main/kotlin/com/nikhil/yt/innertube/pages/NewPipe.kt")
text = newpipe.read_text(encoding="utf-8")
old_import = "import org.schabi.newpipe.extractor.services.youtube.YoutubeJavaScriptPlayerManager"
assert text.count(old_import) == 1, f"newpipe import matches: {text.count(old_import)}"
new_imports = (
    old_import
    + "\nimport org.schabi.newpipe.extractor.ServiceList"
    + "\nimport org.schabi.newpipe.extractor.stream.StreamInfo"
)
text = text.replace(old_import, new_imports)
anchor = "fun getStreamUrl("
assert text.count(anchor) == 1, f"newpipe anchor matches: {text.count(anchor)}"
helper = '''fun getFreshStreamUrl(videoId: String, itag: Int): Result<String> = runCatching {
    val info = StreamInfo.getInfo(
        ServiceList.YouTube,
        "https://www.youtube.com/watch?v=$videoId",
    )
    info.audioStreams
        .firstOrNull { it.itag == itag }
        ?.content
        ?: throw ParsingException("No fresh stream URL for itag $itag")
}

'''
text = text.replace(anchor, helper + anchor)
newpipe.write_text(text, encoding="utf-8")

# 4) Work around AGP 9.1.1 locale extraction failure.
app_gradle = Path("app/build.gradle.kts")
text = app_gradle.read_text(encoding="utf-8")
old = "generateLocaleConfig = true"
assert text.count(old) == 1, f"locale config pattern matches: {text.count(old)}"
app_gradle.write_text(text.replace(old, "generateLocaleConfig = false"), encoding="utf-8")

print("Patch applied")

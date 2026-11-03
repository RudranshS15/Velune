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


# 5) A restored backup must not bring back stale streaming/account settings.
backup_vm = Path("app/src/main/kotlin/com/nikhil/yt/viewmodels/BackupRestoreViewModel.kt")
text = backup_vm.read_text(encoding="utf-8")

blocked = '''private val RESTORE_BLOCKED_KEYS = setOf(
    "playerStreamClient",
    "audioQuality",
    "networkMetered",
    "audioOffload",
    "proxyEnabled",
    "proxyUrl",
    "proxyType",
    "streamBypassProxy",
    "webClientPoTokenEnabled",
    "poToken",
    "poTokenGvs",
    "poTokenPlayer",
    "poTokenSourceUrl",
    "visitorData",
    "dataSyncId",
    "innerTubeCookie",
    "accountName",
    "accountEmail",
    "accountChannelHandle",
    "useVisitorData",
    "useLoginForBrowse",
)
'''

anchor = "data class BackupRestoreProgressUi("
assert text.count(anchor) == 1, f"backup model anchor matches: {text.count(anchor)}"
text = text.replace(anchor, blocked + "\n" + anchor)

# Legacy backups can contain the binary DataStore snapshot. Restoring it would
# overwrite every setting, bypassing the key filter below.
pattern = re.compile(r"[ \t]*name == SETTINGS_FILENAME \|\|\n")
assert len(pattern.findall(text)) == 1, f"legacy settings entry matches: {len(pattern.findall(text))}"
text = pattern.sub("", text)

pattern = re.compile(r"if \(keyName != null\) \{")
assert len(pattern.findall(text)) == 1, f"settings key filter matches: {len(pattern.findall(text))}"
text = pattern.sub("if (keyName != null && keyName !in RESTORE_BLOCKED_KEYS) {", text)

# Remove values already present from an earlier restore, then apply the sanitized XML.
pattern = re.compile(
    r"([ \t]*)emitProgress\(\n"
    r"[ \t]*title = title,\n"
    r"[ \t]*step = context\.getString\(R\.string\.restore_step_restarting\),"
)
match = pattern.search(text)
assert match is not None, "restore completion anchor not found"
text = (
    text[:match.start()]
    + f"{match.group(1)}clearRestoredStreamingSettings(context)\n"
    + text[match.start():]
)

helper_anchor = "    private suspend fun restoreSettingsFromXml(context: Context, inputStream: java.io.InputStream) {"
assert text.count(helper_anchor) == 1, f"restore helper anchor matches: {text.count(helper_anchor)}"
helper = '''    private suspend fun clearRestoredStreamingSettings(context: Context) {
        context.dataStore.edit { prefs ->
            RESTORE_BLOCKED_KEYS.forEach { name ->
                prefs.remove(booleanPreferencesKey(name))
                prefs.remove(intPreferencesKey(name))
                prefs.remove(longPreferencesKey(name))
                prefs.remove(floatPreferencesKey(name))
                prefs.remove(stringPreferencesKey(name))
                prefs.remove(stringSetPreferencesKey(name))
            }
        }
    }

'''
text = text.replace(helper_anchor, helper + helper_anchor)
backup_vm.write_text(text, encoding="utf-8")
print("Restore settings sanitized")

# 6) Error 2000 is ExoPlayer's unspecified IO/source error. The official release
# handles it with the client-cycle fallback; our recovery list omitted it. Retry
# on 2000 and force the direct format URL so the per-client fallback is not
# short-circuited by the NewPipe URL for every client.
music = Path("app/src/main/kotlin/com/nikhil/yt/playback/MusicService.kt")
text = music.read_text(encoding="utf-8")

old = "private val streamRecoveryState = ConcurrentHashMap<String, Pair<Int, Long>>()"
assert text.count(old) == 1, f"recovery state anchor matches: {text.count(old)}"
text = text.replace(
    old,
    old + "\n    private val forceDirectStreamUrlMediaIds = java.util.Collections.synchronizedSet(mutableSetOf<String>())",
)

old = "error.errorCode == PlaybackException.ERROR_CODE_IO_BAD_HTTP_STATUS ||"
assert text.count(old) == 1, f"IO retry anchor matches: {text.count(old)}"
text = text.replace(
    old,
    "error.errorCode == PlaybackException.ERROR_CODE_IO_UNSPECIFIED ||\n                    " + old,
)

old = "avoidCodecs = avoidStreamCodecs,"
assert text.count(old) == 1, f"playback call anchor matches: {text.count(old)}"
text = text.replace(
    old,
    old + "\n                    forceDirectUrl = forceDirectStreamUrlMediaIds.remove(mediaId) == true,",
)

old = (
    "            YTPlayerUtils.markStreamClientFailed(currentMediaId, failingStreamClientKey, httpStatusCode)\n"
    "            YTPlayerUtils.markPreferredClientFailed(currentMediaId, preferredStreamClient, httpStatusCode)\n"
    "            YTPlayerUtils.invalidateCachedStreamUrls(currentMediaId)\n"
    "            playbackUrlCache.remove(currentMediaId)\n"
    "            pendingStreamRefreshValidationMediaId = currentMediaId\n"
)
assert text.count(old) == 1, f"stream refresh block matches: {text.count(old)}"
text = text.replace(
    old,
    old
    + "            if (error.errorCode == PlaybackException.ERROR_CODE_IO_UNSPECIFIED) {\n"
    + "                forceDirectStreamUrlMediaIds.add(currentMediaId)\n"
    + "            }\n",
)
music.write_text(text, encoding="utf-8")

player_utils = Path("app/src/main/kotlin/com/nikhil/yt/utils/YTPlayerUtils.kt")
text = player_utils.read_text(encoding="utf-8")

old = "        avoidCodecs: Set<String> = emptySet(),\n    ): Result<PlaybackData> = runCatching {"
assert text.count(old) == 1, f"playback signature matches: {text.count(old)}"
text = text.replace(
    old,
    "        avoidCodecs: Set<String> = emptySet(),\n        forceDirectUrl: Boolean = false,\n    ): Result<PlaybackData> = runCatching {",
)

old = "                        avoidCodecs = avoidCodecs,\n                    )"
assert text.count(old) == 1, f"playback inner call matches: {text.count(old)}"
text = text.replace(
    old,
    "                        avoidCodecs = avoidCodecs,\n                        forceDirectUrl = forceDirectUrl,\n                    )",
)

old = "        avoidCodecs: Set<String>,\n    ): PlaybackData {"
assert text.count(old) == 1, f"inner signature matches: {text.count(old)}"
text = text.replace(
    old,
    "        avoidCodecs: Set<String>,\n        forceDirectUrl: Boolean,\n    ): PlaybackData {",
)

old = "findUrlOrNull(candidate, videoId, client)"
assert text.count(old) == 1, f"find URL call matches: {text.count(old)}"
text = text.replace(old, "findUrlOrNull(candidate, videoId, client, forceDirectUrl)")

old = "        client: YouTubeClient? = null,\n    ): String? {"
assert text.count(old) == 1, f"find URL signature matches: {text.count(old)}"
text = text.replace(
    old,
    "        client: YouTubeClient? = null,\n        forceDirectUrl: Boolean = false,\n    ): String? {",
)

pattern = re.compile(
    r"^[ \t]*NewPipeUtils\.getFreshStreamUrl\(videoId, format\.itag\)\.getOrNull\(\)\?\.let \{ freshUrl ->\n"
    r"^[ \t]*Timber\.tag\(logTag\)\.i\(\"Using fresh NewPipe stream URL for itag \$\{format\.itag\}\"\)\n"
    r"^[ \t]*return freshUrl\n"
    r"^[ \t]*\}\n"
    r"^[ \t]*var url = NewPipeUtils\.getStreamUrl\(format, videoId, client\)",
    re.M,
)
assert len(pattern.findall(text)) == 1, f"fresh URL block matches: {len(pattern.findall(text))}"
text = pattern.sub(
    '''        if (!forceDirectUrl) {
            NewPipeUtils.getFreshStreamUrl(videoId, format.itag).getOrNull()?.let { freshUrl ->
                Timber.tag(logTag).i("Using fresh NewPipe stream URL for itag ${format.itag}")
                return freshUrl
            }
        }
        var url = NewPipeUtils.getStreamUrl(format, videoId, client)''',
    text,
    count=1,
)
player_utils.write_text(text, encoding="utf-8")
print("Error 2000 retry and direct-URL fallback added")

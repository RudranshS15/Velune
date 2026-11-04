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

# 7) Some tracks fail on every InnerTube client with a generic "Unknown error".
# NewPipe's extractor is a separate path with its own session handling, so use it
# as a last resort instead of surfacing a playback failure.
newpipe = Path("innertube/src/main/kotlin/com/nikhil/yt/innertube/pages/NewPipe.kt")
text = newpipe.read_text(encoding="utf-8")

old_import = "import org.schabi.newpipe.extractor.stream.StreamInfo"
assert text.count(old_import) == 1, f"newpipe stream import matches: {text.count(old_import)}"
text = text.replace(
    old_import,
    old_import + "\nimport org.schabi.newpipe.extractor.stream.AudioStream",
)

old = "fun getStreamUrl(format: PlayerResponse.StreamingData.Format"
assert text.count(old) == 1, f"newpipe getStreamUrl anchor matches: {text.count(old)}"
fallback = '''    fun getFallbackAudioStream(videoId: String): Result<FallbackAudioStream> = runCatching {
        val info = StreamInfo.getInfo(
            ServiceList.YouTube,
            "https://www.youtube.com/watch?v=$videoId",
        )
        val stream = info.audioStreams
            .asSequence()
            .filter { it.isUrl && it.content.isNotBlank() }
            .sortedWith(
                compareByDescending<AudioStream> {
                    it.averageBitrate.takeIf { bitrate -> bitrate > 0 } ?: it.bitrate
                }
            )
            .firstOrNull()
            ?: throw ParsingException("No audio stream available")

        FallbackAudioStream(
            url = stream.content,
            itag = stream.itag,
            bitrate = stream.bitrate.takeIf { it > 0 }
                ?: stream.averageBitrate.takeIf { it > 0 }
                ?: 128_000,
            mimeType = stream.format?.mimeType ?: "audio/mp4",
            codec = stream.codec,
            quality = stream.quality,
            durationSeconds = info.duration.takeIf { it > 0 },
        )
    }

'''
text = text.replace(old, fallback + "    " + old)

old_tail = '''        throw lastError ?: IllegalStateException("Retry attempts exhausted")
    }

}'''
assert text.count(old_tail) == 1, f"newpipe tail matches: {text.count(old_tail)}"
text = text.replace(
    old_tail,
    old_tail + '''

data class FallbackAudioStream(
    val url: String,
    val itag: Int,
    val bitrate: Int,
    val mimeType: String,
    val codec: String?,
    val quality: String?,
    val durationSeconds: Long?,
)''',
)
newpipe.write_text(text, encoding="utf-8")
print("NewPipe fallback resolver added")

# 8) Give NewPipe the signed-in session too, so age-restricted tracks can resolve.
old = '''        if (!hasUserAgent) {
            requestBuilder.header("User-Agent", YouTubeClient.USER_AGENT_WEB)
        }
'''
assert text.count(old) == 1, f"newpipe user-agent block matches: {text.count(old)}"
cookie_block = '''
        val cookie = YouTube.cookie
        if (!cookie.isNullOrBlank() && headers.keys.none { it.equals("Cookie", ignoreCase = true) }) {
            requestBuilder.header("Cookie", cookie)
        }
'''
text = text.replace(old, old + cookie_block)
newpipe.write_text(text, encoding="utf-8")
print("NewPipe session cookies forwarded")

# 9) Wrap InnerTube resolution and fall back to NewPipe on generic resolver errors.
player_utils = Path("app/src/main/kotlin/com/nikhil/yt/utils/YTPlayerUtils.kt")
text = player_utils.read_text(encoding="utf-8")

old_import = "import okhttp3.HttpUrl.Companion.toHttpUrlOrNull"
assert text.count(old_import) == 1, f"player import anchor matches: {text.count(old_import)}"
text = text.replace(old_import, "import kotlinx.coroutines.CancellationException\n" + old_import)

old = "    private suspend fun playerResponseForPlaybackOnce("
assert text.count(old) == 1, f"player once anchor matches: {text.count(old)}"
helper = '''    private fun resolvePlaybackViaNewPipe(
        videoId: String,
        audioQuality: AudioQuality,
    ): PlaybackData? {
        val fallback = NewPipeUtils.getFallbackAudioStream(videoId)
            .onFailure { Timber.tag(logTag).e(it, "NewPipe fallback failed for $videoId") }
            .getOrNull()
            ?: return null

        val defaultCodec = when {
            fallback.mimeType.contains("webm", ignoreCase = true) -> "opus"
            else -> "mp4a.40.2"
        }
        val codec = fallback.codec?.takeIf { it.isNotBlank() } ?: defaultCodec
        val mimeType = if (fallback.mimeType.contains("codecs=", ignoreCase = true)) {
            fallback.mimeType
        } else {
            "${fallback.mimeType}; codecs=\\"$codec\\""
        }

        val format = PlayerResponse.StreamingData.Format(
            itag = fallback.itag,
            url = fallback.url,
            mimeType = mimeType,
            bitrate = fallback.bitrate,
            width = null,
            height = null,
            contentLength = null,
            quality = fallback.quality ?: "unknown",
            fps = null,
            qualityLabel = null,
            averageBitrate = fallback.bitrate,
            audioQuality = null,
            approxDurationMs = fallback.durationSeconds?.times(1000L)?.toString(),
            audioSampleRate = null,
            audioChannels = null,
            loudnessDb = null,
            lastModified = null,
            signatureCipher = null,
            cipher = null,
        )
        val expiresInSeconds = fallback.url.toHttpUrlOrNull()
            ?.queryParameter("expire")?.toLongOrNull()
            ?.let { (it - System.currentTimeMillis() / 1000L).toInt() }
            ?.takeIf { it > 0 } ?: 3600

        Timber.tag(logTag).i(
            "NewPipe fallback resolved audio for $videoId (itag=${fallback.itag}, quality=$audioQuality)"
        )
        streamUrlCache[buildCacheKey(videoId, format.itag)] = CachedStreamUrl(
            url = fallback.url,
            expiresAtMs = System.currentTimeMillis() + expiresInSeconds * 1000L,
        )

        return PlaybackData(
            audioConfig = null,
            videoDetails = null,
            playbackTracking = null,
            format = format,
            streamUrl = fallback.url,
            streamExpiresInSeconds = expiresInSeconds,
        )
    }

'''
text = text.replace(old, helper + old)
player_utils.write_text(text, encoding="utf-8")
print("NewPipe fallback helper added")

old = '''    private suspend fun playerResponseForPlaybackOnce(
        videoId: String,
        playlistId: String?,
        audioQuality: AudioQuality,
        connectivityManager: ConnectivityManager,
        preferredStreamClient: PlayerStreamClient,
        networkMetered: Boolean?,
        avoidCodecs: Set<String>,
        forceDirectUrl: Boolean,
    ): PlaybackData {
        Timber.tag(logTag).i("Fetching player response for videoId: $videoId, playlistId: $playlistId")'''
assert text.count(old) == 1, f"player once signature matches: {text.count(old)}"
replacement = '''    private suspend fun playerResponseForPlaybackOnce(
        videoId: String,
        playlistId: String?,
        audioQuality: AudioQuality,
        connectivityManager: ConnectivityManager,
        preferredStreamClient: PlayerStreamClient,
        networkMetered: Boolean?,
        avoidCodecs: Set<String>,
        forceDirectUrl: Boolean,
    ): PlaybackData {
        return try {
            resolvePlaybackViaInnerTube(
                videoId = videoId,
                playlistId = playlistId,
                audioQuality = audioQuality,
                connectivityManager = connectivityManager,
                preferredStreamClient = preferredStreamClient,
                networkMetered = networkMetered,
                avoidCodecs = avoidCodecs,
                forceDirectUrl = forceDirectUrl,
            )
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            Timber.tag(logTag).w(error, "InnerTube playback resolution failed for $videoId; trying NewPipe fallback")
            resolvePlaybackViaNewPipe(videoId, audioQuality) ?: throw error
        }
    }

    private suspend fun resolvePlaybackViaInnerTube(
        videoId: String,
        playlistId: String?,
        audioQuality: AudioQuality,
        connectivityManager: ConnectivityManager,
        preferredStreamClient: PlayerStreamClient,
        networkMetered: Boolean?,
        avoidCodecs: Set<String>,
        forceDirectUrl: Boolean,
    ): PlaybackData {
        Timber.tag(logTag).i("Fetching player response for videoId: $videoId, playlistId: $playlistId")'''
text = text.replace(old, replacement)
player_utils.write_text(text, encoding="utf-8")
print("NewPipe playback fallback added")

# 10) Ship as its own app so it installs beside the stock app and the earlier
# patched build instead of trying to update one of them (different package).
app_gradle = Path("app/build.gradle.kts")
text = app_gradle.read_text(encoding="utf-8")
old = 'applicationIdSuffix = ".debug"'
assert text.count(old) == 1, f"applicationIdSuffix matches: {text.count(old)}"
text = text.replace(old, 'applicationIdSuffix = ".v5"')
app_gradle.write_text(text, encoding="utf-8")

label = Path("app/src/debug/res/values/app_name.xml")
label.parent.mkdir(parents=True, exist_ok=True)
label.write_text(
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<resources>\n'
    '    <string name="app_name">Velune v5</string>\n'
    '</resources>\n',
    encoding="utf-8",
)
print("Separate app identity applied: com.nikhil.yt.v5 / Velune v5")

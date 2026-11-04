/*
 * Velune Project Original (2026)
 * Kòi Natsuko (github.com/koiverse)
 * Licensed Under GPL-3.0 | see git history for contributors
 */



package com.nikhil.yt.innertube.pages

import com.nikhil.yt.innertube.YouTube
import com.nikhil.yt.innertube.models.YouTubeClient
import com.nikhil.yt.innertube.models.response.PlayerResponse
import io.ktor.http.URLBuilder
import io.ktor.http.parseQueryString
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.exceptions.ParsingException
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException
import org.schabi.newpipe.extractor.services.youtube.YoutubeJavaScriptPlayerManager
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.AudioStream
import java.io.IOException
import java.net.Proxy
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

private class NewPipeDownloaderImpl(proxy: Proxy?) : Downloader() {

    @Volatile
    var cookieEnabled: Boolean = true

    private val client = OkHttpClient.Builder()
        .proxy(proxy)
        .retryOnConnectionFailure(true)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(45, TimeUnit.SECONDS)
        .build()

    @Throws(IOException::class, ReCaptchaException::class)
    override fun execute(request: Request): Response {
        val httpMethod = request.httpMethod()
        val url = request.url()
        val headers = request.headers()
        val dataToSend = request.dataToSend()

        val requestBuilder = okhttp3.Request.Builder()
            .method(httpMethod, dataToSend?.toRequestBody())
            .url(url)

        var hasUserAgent = false
        headers.forEach { (headerName, headerValueList) ->
            if (headerName.equals("User-Agent", ignoreCase = true) && headerValueList.isNotEmpty()) {
                hasUserAgent = true
            }
            if (headerValueList.size > 1) {
                requestBuilder.removeHeader(headerName)
                headerValueList.forEach { headerValue ->
                    requestBuilder.addHeader(headerName, headerValue)
                }
            } else if (headerValueList.size == 1) {
                requestBuilder.header(headerName, headerValueList[0])
            }
        }

        if (!hasUserAgent) {
            requestBuilder.header("User-Agent", YouTubeClient.USER_AGENT_WEB)
        }

        val cookie = YouTube.cookie
        val injectedCookie = cookieEnabled && !cookie.isNullOrBlank() &&
            headers.keys.none { it.equals("Cookie", ignoreCase = true) }
        if (injectedCookie) {
            requestBuilder.header("Cookie", cookie!!)
        }

        val builtRequest = requestBuilder.build()
        var response = client.newCall(builtRequest).execute()

        // A stale or partial session cookie makes the extractor fail with 401/403.
        // Retry the same request anonymously before reporting a failure.
        if (injectedCookie && response.code in 401..403) {
            response.close()
            response = client.newCall(
                builtRequest.newBuilder().removeHeader("Cookie").build()
            ).execute()
        }

        if (response.code == 429) {
            response.close()

            throw ReCaptchaException("reCaptcha Challenge requested", url)
        }

        val responseBodyToReturn = response.body.string()

        val latestUrl = response.request.url.toString()
        return Response(response.code, response.message, response.headers.toMultimap(), responseBodyToReturn, latestUrl)
    }

}

object NewPipeUtils {

    private val downloader = NewPipeDownloaderImpl(YouTube.proxy)

    init {
        NewPipe.init(downloader)
    }

    fun getSignatureTimestamp(videoId: String): Result<Int> = runCatching {
        YoutubeJavaScriptPlayerManager.getSignatureTimestamp(videoId)
    }

    fun getFreshStreamUrl(videoId: String, itag: Int): Result<String> = runCatching {
    val info = StreamInfo.getInfo(
        ServiceList.YouTube,
        "https://www.youtube.com/watch?v=$videoId",
    )
    info.audioStreams
        .firstOrNull { it.itag == itag }
        ?.content
        ?: throw ParsingException("No fresh stream URL for itag $itag")
}

    fun getFallbackAudioStream(videoId: String): Result<FallbackAudioStream> {
        val authenticated = runCatching { extractFallbackAudioStream(videoId) }
        if (authenticated.isSuccess) return authenticated

        downloader.cookieEnabled = false
        val anonymous = try {
            runCatching { extractFallbackAudioStream(videoId) }
        } finally {
            downloader.cookieEnabled = true
        }
        authenticated.exceptionOrNull()?.let { first ->
            anonymous.exceptionOrNull()?.addSuppressed(first)
        }
        return anonymous
    }

    private fun extractFallbackAudioStream(videoId: String): FallbackAudioStream {
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

        return FallbackAudioStream(
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

    fun getStreamUrl(format: PlayerResponse.StreamingData.Format, videoId: String, client: YouTubeClient? = null): Result<String> =
        runCatching {
            val url = format.url ?: run {
                val cipherString = format.signatureCipher ?: format.cipher
                if (cipherString == null) throw ParsingException("Could not find format url")

                val params = parseQueryString(cipherString)
                val obfuscatedSignature = params["s"]
                    ?: throw ParsingException("Could not parse cipher signature")
                val signatureParam = params["sp"]
                    ?: throw ParsingException("Could not parse cipher signature parameter")
                val url = params["url"]?.let { URLBuilder(it) }
                    ?: throw ParsingException("Could not parse cipher url")
                url.parameters[signatureParam] =
                    YoutubeJavaScriptPlayerManager.deobfuscateSignature(
                        videoId,
                        obfuscatedSignature
                    )
                url.toString()
            }

            val resolvedUrl = runCatching {
                retryWithBackoff(
                    maxAttempts = 3,
                    initialDelayMs = 250L,
                    maxDelayMs = 2_000L
                ) {
                    YoutubeJavaScriptPlayerManager.getUrlWithThrottlingParameterDeobfuscated(videoId, url)
                }
            }.getOrElse { url }

            YouTube.appendGvsPoToken(resolvedUrl, client)
        }

    private inline fun <T> retryWithBackoff(
        maxAttempts: Int,
        initialDelayMs: Long,
        maxDelayMs: Long,
        block: () -> T
    ): T {
        var attempt = 0
        var delayMs = initialDelayMs
        var lastError: Throwable? = null
        while (attempt < maxAttempts) {
            try {
                return block()
            } catch (e: Throwable) {
                val isRetryable =
                    e is SocketTimeoutException ||
                        e is IOException ||
                        e.cause is SocketTimeoutException ||
                        e.cause is IOException
                if (!isRetryable || attempt == maxAttempts - 1) throw e
                lastError = e
                try {
                    Thread.sleep(delayMs)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw e
                }
                delayMs = (delayMs * 2).coerceAtMost(maxDelayMs)
                attempt++
            }
        }
        throw lastError ?: IllegalStateException("Retry attempts exhausted")
    }

}

data class FallbackAudioStream(
    val url: String,
    val itag: Int,
    val bitrate: Int,
    val mimeType: String,
    val codec: String?,
    val quality: String?,
    val durationSeconds: Long?,
)

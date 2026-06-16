import { describe, expect, it } from 'vitest'
import { handleVoiceMessage } from './bot.js'

describe('handleVoiceMessage', () => {
  it('returns early when speech-to-text is not configured', async () => {
    const replies: string[] = []
    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async (message: string) => {
        replies.push(message)
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: false, tts: false }),
      downloadMedia: async () => {
        throw new Error('downloadMedia should not be called')
      },
      transcribeAudio: async () => {
        throw new Error('transcribeAudio should not be called')
      },
      handleMessage: async () => {
        throw new Error('handleMessage should not be called')
      },
      logVoiceError: () => undefined,
    })

    expect(replies).toEqual(['Voice transcription is not configured.'])
  })

  it('uses the retry-enabled media downloader directly for Telegram voice file ids', async () => {
    const replies: string[] = []
    const handledMessages: string[] = []
    const downloaded: string[] = []
    const events: string[] = []
    let handledCtx: unknown

    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async (message: string) => {
        replies.push(message)
        events.push(`reply:${message}`)
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: true, tts: false }),
      downloadMedia: async (fileId) => {
        downloaded.push(fileId)
        return '/tmp/telegram-voice.ogg'
      },
      transcribeAudio: async (path) => {
        expect(path).toBe('/tmp/telegram-voice.ogg')
        return 'voice memo text'
      },
      handleMessage: async (messageCtx, message) => {
        handledCtx = messageCtx
        handledMessages.push(message)
        events.push(`handle:${message}`)
      },
      logVoiceError: () => undefined,
    })

    expect(downloaded).toEqual(['telegram-voice-file-id'])
    expect(replies).toEqual(['[Transcribed]: voice memo text'])
    expect(handledMessages).toEqual(['[Voice transcribed]: voice memo text'])
    expect(handledCtx).toBe(ctx)
    expect(events).toEqual([
      'reply:[Transcribed]: voice memo text',
      'handle:[Voice transcribed]: voice memo text',
    ])
  })

  it('keeps the transcript visible when downstream message processing fails', async () => {
    const replies: string[] = []
    const logged: string[] = []
    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async (message: string) => {
        replies.push(message)
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: true, tts: false }),
      downloadMedia: async () => '/tmp/telegram-voice.ogg',
      transcribeAudio: async () => 'voice memo text',
      handleMessage: async () => {
        throw new Error('agent failed')
      },
      logVoiceError: (stage, err) => {
        logged.push(`${stage}:${String(err)}`)
      },
    })

    expect(replies).toEqual([
      '[Transcribed]: voice memo text',
      'Transcribed, but processing failed. Try typing the transcribed text above.',
    ])
    expect(logged).toHaveLength(1)
    expect(logged[0]).toContain('processing:')
    expect(logged[0]).toContain('agent failed')
  })

  it('logs transcript reply failures and still processes the transcript', async () => {
    const handledMessages: string[] = []
    const logged: string[] = []
    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async () => {
        throw new Error('telegram reply failed')
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: true, tts: false }),
      downloadMedia: async () => '/tmp/telegram-voice.ogg',
      transcribeAudio: async () => 'voice memo text',
      handleMessage: async (_ctx, message) => {
        handledMessages.push(message)
      },
      logVoiceError: (stage, err) => {
        logged.push(`${stage}:${String(err)}`)
      },
    })

    expect(handledMessages).toEqual(['[Voice transcribed]: voice memo text'])
    expect(logged).toHaveLength(1)
    expect(logged[0]).toContain('reply:')
    expect(logged[0]).toContain('telegram reply failed')
  })

  it('reports download failures without transcribing or processing', async () => {
    const replies: string[] = []
    const logged: string[] = []
    let transcribeCalled = false
    let handleCalled = false
    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async (message: string) => {
        replies.push(message)
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: true, tts: false }),
      downloadMedia: async () => {
        const err = new Error('permission denied') as NodeJS.ErrnoException
        err.code = 'EACCES'
        throw err
      },
      transcribeAudio: async () => {
        transcribeCalled = true
        return 'should not happen'
      },
      handleMessage: async () => {
        handleCalled = true
      },
      logVoiceError: (stage, err) => {
        logged.push(`${stage}:${String(err)}`)
      },
    })

    expect(replies).toEqual(['Failed to transcribe voice note. Try again or type your message.'])
    expect(logged).toHaveLength(1)
    expect(logged[0]).toContain('download:')
    expect(logged[0]).toContain('permission denied')
    expect(transcribeCalled).toBe(false)
    expect(handleCalled).toBe(false)
  })

  it('reports transcription failures without handing text to the agent', async () => {
    const replies: string[] = []
    const logged: string[] = []
    let handleCalled = false
    const ctx = {
      message: { voice: { file_id: 'telegram-voice-file-id' } },
      reply: async (message: string) => {
        replies.push(message)
      },
    } as unknown as Parameters<typeof handleVoiceMessage>[0]

    await handleVoiceMessage(ctx, {
      voiceCapabilities: () => ({ stt: true, tts: false }),
      downloadMedia: async () => '/tmp/telegram-voice.ogg',
      transcribeAudio: async () => {
        throw new Error('stt unavailable')
      },
      handleMessage: async () => {
        handleCalled = true
      },
      logVoiceError: (stage, err) => {
        logged.push(`${stage}:${String(err)}`)
      },
    })

    expect(replies).toEqual(['Failed to transcribe voice note. Try again or type your message.'])
    expect(logged).toHaveLength(1)
    expect(logged[0]).toContain('transcription:')
    expect(logged[0]).toContain('stt unavailable')
    expect(handleCalled).toBe(false)
  })
})

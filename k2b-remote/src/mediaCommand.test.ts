import { describe, expect, it } from 'vitest'
import { K2B_VAULT_PATH } from './config.js'
import {
  extractGeneratedAudioPath,
  extractGeneratedImagePath,
  parseMediaCommand,
  safeUserErrorMessage,
  splitCommandArgs,
} from './mediaCommand.js'

describe('splitCommandArgs', () => {
  it('keeps quoted prompts as one argument', () => {
    expect(splitCommandArgs('/media image "Clean executive K2B logo" 1:1 logo')).toEqual([
      '/media',
      'image',
      'Clean executive K2B logo',
      '1:1',
      'logo',
    ])
  })

  it('handles escaped quotes inside prompts', () => {
    expect(splitCommandArgs('/media image "Headline says \\"K2B\\""')).toEqual([
      '/media',
      'image',
      'Headline says "K2B"',
    ])
  })

  it('preserves ordinary backslashes in prompts', () => {
    expect(splitCommandArgs('/media image "C:\\Users\\Keith\\deck mockup"')).toEqual([
      '/media',
      'image',
      'C:\\Users\\Keith\\deck mockup',
    ])
  })

  it('preserves consecutive backslashes before text', () => {
    expect(splitCommandArgs(String.raw`/media image "C:\\Users\\Keith\\deck"`)).toEqual([
      '/media',
      'image',
      String.raw`C:\\Users\\Keith\\deck`,
    ])
  })

  it('handles escaped quotes after backslashes', () => {
    expect(splitCommandArgs(String.raw`/media image "folder\\\"quoted\""`)).toEqual([
      '/media',
      'image',
      String.raw`folder\"quoted"`,
    ])
  })
})

describe('parseMediaCommand', () => {
  it('routes /media image to gptsapi by default', () => {
    expect(parseMediaCommand('/media image "Clean executive K2B logo" 1:1 logo')).toEqual({
      ok: true,
      request: {
        kind: 'image',
        provider: 'gptsapi',
        prompt: 'Clean executive K2B logo',
        aspectRatio: '1:1',
        slug: 'logo',
      },
    })
  })

  it('uses MiniMax when --minimax is present', () => {
    expect(parseMediaCommand('/media image --minimax "clean stylized sketch" 21:9 story')).toEqual({
      ok: true,
      request: {
        kind: 'image',
        provider: 'minimax',
        prompt: 'clean stylized sketch',
        aspectRatio: '21:9',
        slug: 'story',
      },
    })
  })

  it('rejects MiniMax-only aspect ratios on the GPTsAPI default path', () => {
    const parsed = parseMediaCommand('/media image "wide clean logo" 21:9')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toContain('not supported')
  })

  it('joins unquoted prompt words when no aspect ratio is supplied', () => {
    expect(parseMediaCommand('/media image clean executive logo')).toEqual({
      ok: true,
      request: {
        kind: 'image',
        provider: 'gptsapi',
        prompt: 'clean executive logo',
        aspectRatio: '16:9',
      },
    })
  })

  it('parses speech with defaults', () => {
    expect(parseMediaCommand('/media speech "hello world"')).toEqual({
      ok: true,
      request: {
        kind: 'speech',
        text: 'hello world',
        voice: 'onyx',
        model: 'tts-1-hd',
      },
    })
  })

  it('consumes the optional speech voice', () => {
    expect(parseMediaCommand('/media speech "hello" nova')).toEqual({
      ok: true,
      request: {
        kind: 'speech',
        text: 'hello',
        voice: 'nova',
        model: 'tts-1-hd',
      },
    })
  })

  it('consumes the optional speech voice and model', () => {
    expect(parseMediaCommand('/media speech "hello" nova tts-1')).toEqual({
      ok: true,
      request: {
        kind: 'speech',
        text: 'hello',
        voice: 'nova',
        model: 'tts-1',
      },
    })
  })

  it('consumes the optional speech voice, model, and slug', () => {
    expect(parseMediaCommand('/media speech "hello" nova tts-1 my-clip')).toEqual({
      ok: true,
      request: {
        kind: 'speech',
        text: 'hello',
        voice: 'nova',
        model: 'tts-1',
        slug: 'my-clip',
      },
    })
  })

  it('keeps a bare non-voice token as the speech slug', () => {
    expect(parseMediaCommand('/media speech "hello" my-clip')).toEqual({
      ok: true,
      request: {
        kind: 'speech',
        text: 'hello',
        voice: 'onyx',
        model: 'tts-1-hd',
        slug: 'my-clip',
      },
    })
  })

  it('rejects unquoted speech text', () => {
    const parsed = parseMediaCommand('/media speech hello world')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toBe('Quote the text when using /media speech: /media speech "text" [voice] [model] [slug]')
  })

  it('rejects unsupported speech voices', () => {
    const parsed = parseMediaCommand('/media speech "hello" badvoice')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toBe('Voice badvoice is not supported. Choose one of: alloy, echo, fable, onyx, nova, shimmer.')
  })

  it('rejects unsupported speech models', () => {
    const parsed = parseMediaCommand('/media speech "hello" onyx badmodel')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toBe('Model badmodel is not supported. Choose one of: tts-1, tts-1-hd.')
  })

  it('rejects shell metacharacters in speech text before invoking wrapper scripts', () => {
    const parsed = parseMediaCommand('/media speech "hello $(touch /tmp/x)"')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toBe('Speech text contains unsupported shell metacharacters.')
  })

  it('rejects unsupported media subcommands with the updated Telegram wiring message', () => {
    for (const command of ['/media transcribe x', '/media video x', '/media music x']) {
      const parsed = parseMediaCommand(command)
      expect(parsed.ok).toBe(false)
      expect(parsed.message).toBe(
        'Only /media image and /media speech are wired in Telegram. Use the MacBook session for video, music, and transcription.'
      )
    }
  })

  it('rejects unquoted prompts when an aspect ratio token is present', () => {
    const parsed = parseMediaCommand('/media image draw a 16:9 grid')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toContain('Quote the prompt')
  })

  it('keeps aspect-ratio text inside quoted prompts', () => {
    expect(parseMediaCommand('/media image "draw a 16:9 grid"')).toEqual({
      ok: true,
      request: {
        kind: 'image',
        provider: 'gptsapi',
        prompt: 'draw a 16:9 grid',
        aspectRatio: '16:9',
      },
    })
  })

  it('rejects empty quoted prompts', () => {
    const parsed = parseMediaCommand('/media image ""')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toBe('Image prompt is missing.')
  })

  it('rejects shell metacharacters before invoking wrapper scripts', () => {
    const parsed = parseMediaCommand('/media image "logo $(touch /tmp/nope)"')
    expect(parsed.ok).toBe(false)
    expect(parsed.message).toContain('unsupported shell metacharacters')
  })
})

describe('extractGeneratedImagePath', () => {
  it('requires the Saved prefix for gptsapi stdout paths', () => {
    expect(extractGeneratedImagePath('/tmp/k2b-gptsapi-smoke.png\n')).toBeUndefined()
    expect(extractGeneratedImagePath('Saved: /tmp/k2b-gptsapi-smoke.png\n')).toBe('/tmp/k2b-gptsapi-smoke.png')
  })

  it('reads the MiniMax saved line', () => {
    expect(extractGeneratedImagePath('Saved: /tmp/minimax.png\nVault path: Assets/images/minimax.png\n')).toBe(
      '/tmp/minimax.png'
    )
  })

  it('ignores absolute non-image lines', () => {
    expect(extractGeneratedImagePath('/tmp/not-an-image.txt\nSaved: /tmp/real.jpeg\n')).toBe('/tmp/real.jpeg')
  })

  it('rejects saved paths outside the vault and /tmp', () => {
    expect(extractGeneratedImagePath('Saved: /etc/passwd.png\n')).toBeUndefined()
  })

  it('rejects traversal paths inside allowed roots', () => {
    expect(extractGeneratedImagePath('Saved: /tmp/../etc/passwd.png\n')).toBeUndefined()
    expect(extractGeneratedImagePath(`Saved: ${K2B_VAULT_PATH}/../outside.png\n`)).toBeUndefined()
  })
})

describe('extractGeneratedAudioPath', () => {
  it('reads mp3 paths from the Saved line', () => {
    expect(extractGeneratedAudioPath('Saved: /tmp/clip.mp3\nVault path: Assets/audio/clip.mp3\n')).toBe('/tmp/clip.mp3')
  })

  it('rejects non-mp3 saved paths', () => {
    expect(extractGeneratedAudioPath('Saved: /tmp/not-audio.txt\n')).toBeUndefined()
  })
})

describe('safeUserErrorMessage', () => {
  it('redacts key assignments, bearer tokens, and URL credentials from stack text', () => {
    const raw =
      'Error: export GPTSAPI_KEY=sk-test-secret\n' +
      'Authorization: Bearer sk-test-secret\n' +
      'endpoint https://user:pass@example.test/v1\n' +
      '    at run (/tmp/file.js:1:1)'

    const safe = safeUserErrorMessage(raw)
    expect(safe).not.toContain('sk-test-secret')
    expect(safe).not.toContain('user:pass')
    expect(safe).toContain('GPTSAPI_KEY=<redacted>')
    expect(safe).toContain('Bearer <redacted>')
    expect(safe).toContain('https://<redacted>@example.test/v1')
  })
})

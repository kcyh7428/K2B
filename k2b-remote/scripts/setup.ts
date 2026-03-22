import { execSync, spawnSync } from 'node:child_process'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createInterface } from 'node:readline'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(__dirname, '..')

const GREEN = '\x1b[32m'
const YELLOW = '\x1b[33m'
const RED = '\x1b[31m'
const BOLD = '\x1b[1m'
const RESET = '\x1b[0m'

function ok(msg: string) { console.log(`  ${GREEN}\u2713${RESET} ${msg}`) }
function warn(msg: string) { console.log(`  ${YELLOW}\u26a0${RESET} ${msg}`) }
function fail(msg: string) { console.log(`  ${RED}\u2717${RESET} ${msg}`) }

async function prompt(question: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  return new Promise((resolvePromise) => {
    rl.question(`  ${question}: `, (answer) => {
      rl.close()
      resolvePromise(answer.trim())
    })
  })
}

async function main(): Promise<void> {
  console.log(`
${BOLD}
  ╔═══════════════════════════════╗
  ║    K2B Remote Setup Wizard    ║
  ╚═══════════════════════════════╝
${RESET}`)

  // Step 1: Check requirements
  console.log(`\n${BOLD}Checking requirements...${RESET}\n`)

  // Node version
  const nodeVersion = process.version
  const major = parseInt(nodeVersion.slice(1).split('.')[0])
  if (major >= 20) {
    ok(`Node.js ${nodeVersion}`)
  } else {
    fail(`Node.js ${nodeVersion} -- need v20+`)
    process.exit(1)
  }

  // Claude CLI
  try {
    const claudeVersion = execSync('claude --version 2>/dev/null', { encoding: 'utf-8' }).trim()
    ok(`Claude CLI: ${claudeVersion}`)
  } catch {
    fail('Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code')
    process.exit(1)
  }

  // Build
  console.log(`\n${BOLD}Building project...${RESET}\n`)
  try {
    execSync('npm run build', { cwd: PROJECT_ROOT, stdio: 'pipe' })
    ok('TypeScript build successful')
  } catch (err) {
    fail('Build failed')
    console.error((err as { stderr?: Buffer }).stderr?.toString())
    process.exit(1)
  }

  // Step 2: Collect config
  console.log(`\n${BOLD}Configuration${RESET}\n`)

  const envPath = resolve(PROJECT_ROOT, '.env')
  const existing: Record<string, string> = {}

  if (existsSync(envPath)) {
    const content = readFileSync(envPath, 'utf-8')
    for (const line of content.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eq = trimmed.indexOf('=')
      if (eq > 0) {
        existing[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim()
      }
    }
  }

  // Telegram bot token
  let botToken = existing['TELEGRAM_BOT_TOKEN'] ?? ''
  if (botToken) {
    ok(`Telegram bot token: ...${botToken.slice(-8)}`)
    const change = await prompt('Keep this token? (y/n)')
    if (change.toLowerCase() === 'n') {
      botToken = ''
    }
  }
  if (!botToken) {
    console.log(`\n  To get a Telegram bot token:`)
    console.log(`  1. Open Telegram and search for @BotFather`)
    console.log(`  2. Send /newbot`)
    console.log(`  3. Choose a name: K2B`)
    console.log(`  4. Choose a username: k2b_keith_bot`)
    console.log(`  5. Copy the token BotFather gives you\n`)
    botToken = await prompt('Paste your Telegram bot token')
  }

  // Chat ID
  let chatId = existing['ALLOWED_CHAT_ID'] ?? ''
  if (chatId) {
    ok(`Chat ID: ${chatId}`)
    const change = await prompt('Keep this chat ID? (y/n)')
    if (change.toLowerCase() === 'n') {
      chatId = ''
    }
  }
  if (!chatId) {
    console.log(`\n  To get your chat ID:`)
    console.log(`  1. Start the bot (npm run dev)`)
    console.log(`  2. Send /chatid to your bot in Telegram`)
    console.log(`  3. It will reply with your numeric chat ID`)
    console.log(`  (Leave blank for now -- you can add it after first run)\n`)
    chatId = await prompt('Your Telegram chat ID (or press Enter to skip)')
  }

  // Groq API key
  let groqKey = existing['GROQ_API_KEY'] ?? ''
  if (groqKey) {
    ok(`Groq API key: ...${groqKey.slice(-6)}`)
  } else {
    console.log(`\n  For voice note transcription (optional):`)
    console.log(`  1. Go to console.groq.com`)
    console.log(`  2. Sign up (free)`)
    console.log(`  3. Create an API key\n`)
    groqKey = await prompt('Groq API key (or press Enter to skip)')
  }

  // Step 3: Write .env
  const envContent = [
    '# K2B Remote Configuration',
    '',
    '# Telegram bot token from @BotFather',
    `TELEGRAM_BOT_TOKEN=${botToken}`,
    '',
    '# Your Telegram numeric chat ID',
    `ALLOWED_CHAT_ID=${chatId}`,
    '',
    '# Groq API key for voice transcription',
    `GROQ_API_KEY=${groqKey}`,
    '',
    '# Log level',
    'LOG_LEVEL=info',
    '',
  ].join('\n')

  writeFileSync(envPath, envContent)
  ok('Configuration saved to .env')

  // Step 4: Install as launchd service
  console.log(`\n${BOLD}Background Service${RESET}\n`)
  const installService = await prompt('Install as background service (starts on boot)? (y/n)')

  if (installService.toLowerCase() === 'y') {
    const plistName = 'com.k2b-remote.app'
    const plistPath = resolve(process.env.HOME ?? '~', 'Library', 'LaunchAgents', `${plistName}.plist`)
    const nodePath = execSync('which node', { encoding: 'utf-8' }).trim()
    const distIndex = resolve(PROJECT_ROOT, 'dist', 'index.js')

    const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${plistName}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${nodePath}</string>
        <string>${distIndex}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>/tmp/k2b-remote.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/k2b-remote.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>`

    writeFileSync(plistPath, plist)

    try {
      execSync(`launchctl unload "${plistPath}" 2>/dev/null`, { stdio: 'pipe' })
    } catch {
      // might not be loaded yet
    }
    execSync(`launchctl load "${plistPath}"`)

    ok(`Service installed at ${plistPath}`)
    ok('K2B Remote will start on boot and auto-restart on crash')
    console.log(`  Logs: tail -f /tmp/k2b-remote.log`)
  } else {
    warn('Skipped service install. Run manually with: npm run dev')
  }

  // Done
  console.log(`\n${BOLD}${GREEN}Setup complete!${RESET}\n`)

  if (!chatId) {
    console.log(`  ${YELLOW}Next step:${RESET}`)
    console.log(`  1. Run: npm run dev`)
    console.log(`  2. Send /chatid to your bot in Telegram`)
    console.log(`  3. Add the chat ID to .env as ALLOWED_CHAT_ID`)
    console.log(`  4. Restart the bot\n`)
  } else {
    console.log(`  ${GREEN}Ready to go!${RESET}`)
    console.log(`  Run: npm run dev`)
    console.log(`  Or if you installed the service, it's already running.\n`)
  }

  console.log(`  Commands in Telegram:`)
  console.log(`  /daily    -- Create today's daily note`)
  console.log(`  /standup  -- Status briefing`)
  console.log(`  /memory   -- View recent memories`)
  console.log(`  /newchat  -- Start fresh session`)
  console.log(`  /chatid   -- Show your chat ID\n`)
}

main().catch(console.error)

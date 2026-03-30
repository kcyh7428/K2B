// ecosystem.config.cjs -- pm2 configuration for k2b-remote
// Parameterized for V2B replication: change BRAIN_ID and paths

const BRAIN_ID = process.env.BRAIN_ID || 'k2b';
const PROJECT_ROOT = process.env.PROJECT_ROOT || '/Users/fastshower/Projects/K2B';
const VAULT_PATH = process.env.VAULT_PATH || '/Users/fastshower/Projects/K2B-Vault';

module.exports = {
  apps: [{
    name: `${BRAIN_ID}-remote`,
    script: 'dist/index.js',
    cwd: `${PROJECT_ROOT}/k2b-remote`,
    node_args: '--max-old-space-size=512',
    env: {
      NODE_ENV: 'production',
      LOG_LEVEL: 'info',
      CLAUDE_PROJECT_ROOT: PROJECT_ROOT,
      VAULT_PATH: VAULT_PATH,
      GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND: "file",
      MINIMAX_API_KEY: process.env.MINIMAX_API_KEY || "",
      HTTP_PROXY: process.env.HTTP_PROXY || "http://127.0.0.1:7897",
      HTTPS_PROXY: process.env.HTTPS_PROXY || "http://127.0.0.1:7897",
      NO_PROXY: "localhost,127.0.0.1",
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 5000,
    max_memory_restart: '512M',
    log_date_format: 'YYYY-MM-DD HH:mm:ss',
    error_file: `${PROJECT_ROOT}/k2b-remote/store/logs/${BRAIN_ID}-error.log`,
    out_file: `${PROJECT_ROOT}/k2b-remote/store/logs/${BRAIN_ID}-out.log`,
    merge_logs: true,
    watch: false,
  }]
};

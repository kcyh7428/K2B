import { execSync } from 'child_process'
import { config } from './config.js'

export interface CommitInfo {
  hash: string
  message: string
  date: string
}

export interface LastCommitInfo extends CommitInfo {
  uncommittedChanges: boolean
}

export function getRecentCommits(limit?: number): CommitInfo[] {
  const n = limit || 10
  try {
    const raw = execSync(`git log --format="%H|%s|%ai" -n ${n}`, {
      cwd: config.projectPath,
      encoding: 'utf-8',
      timeout: 5000,
    }).trim()

    if (!raw) return []

    return raw.split('\n').map((line) => {
      const [hash, message, date] = line.split('|')
      return { hash: hash || '', message: message || '', date: date || '' }
    })
  } catch {
    return []
  }
}

export function getLastCommit(): LastCommitInfo | null {
  try {
    const commits = getRecentCommits(1)
    if (commits.length === 0) return null

    const porcelain = execSync('git status --porcelain', {
      cwd: config.projectPath,
      encoding: 'utf-8',
      timeout: 5000,
    }).trim()

    return {
      ...commits[0],
      uncommittedChanges: porcelain.length > 0,
    }
  } catch {
    return null
  }
}

const fs = require('fs')

function extractCurrentValue(readmeText, regex, fallback = 0) {
  const match = readmeText.match(regex)
  return match ? Number(match[1]) : fallback
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function runRequest(github, headers, core, route, params, { attempts = 3, fallback = null } = {}) {
  let lastError

  for (let i = 1; i <= attempts; i++) {
    try {
      return await github.request(route, {
        ...params,
        headers: {
          ...headers,
          ...(params?.headers || {}),
        },
      })
    } catch (error) {
      lastError = error
      core.warning(`Request attempt ${i}/${attempts} failed for ${route}: ${error.message}`)
      if (i < attempts) {
        await sleep(1000 * i)
      }
    }
  }

  if (fallback !== null) {
    core.warning(`Falling back for ${route} due to failure: ${lastError?.message || 'unknown error'}`)
    return fallback
  }

  throw lastError
}

async function run({ github, core, context }) {
  const token = process.env.STATS_TOKEN
  if (!token) {
    core.setFailed('Missing token: define USER_TOKEN or allow GITHUB_TOKEN fallback')
    return
  }

  const headers = { authorization: `token ${token}` }
  const readmePath = `./${process.env.TARGET_FILE || 'README.md'}`
  const templatePath = './TEMPLATE.md'
  const readmeText = fs.existsSync(readmePath) ? fs.readFileSync(readmePath, 'utf8') : ''

  const current = {
    ISSUES: extractCurrentValue(readmeText, /I've opened\s+(\d+)\s+issues/i),
    PULL_REQUESTS: extractCurrentValue(readmeText, /I've contributed with\s+(\d+)\s+pull requests/i),
    COMMITS: extractCurrentValue(readmeText, /I've made\s+(\d+)\s+commits/i),
    REPOSITORIES_CONTRIBUTED_TO: extractCurrentValue(readmeText, /distributed amongst\s+(\d+)\s+repos/i),
  }

  const owner = context.repo.owner

  const issuesRes = await runRequest(
    github,
    headers,
    core,
    'GET /search/issues',
    { q: `author:${owner} is:issue`, per_page: 1 },
    { fallback: { data: { total_count: current.ISSUES } } }
  )

  const prsRes = await runRequest(
    github,
    headers,
    core,
    'GET /search/issues',
    { q: `author:${owner} is:pr`, per_page: 1 },
    { fallback: { data: { total_count: current.PULL_REQUESTS } } }
  )

  const commitsRes = await runRequest(
    github,
    headers,
    core,
    'GET /search/commits',
    {
      q: `author:${owner}`,
      per_page: 1,
      headers: { accept: 'application/vnd.github.cloak-preview+json' },
    },
    { fallback: { data: { total_count: current.COMMITS } } }
  )

  const replacements = {
    ISSUES: Number(issuesRes?.data?.total_count ?? current.ISSUES),
    PULL_REQUESTS: Number(prsRes?.data?.total_count ?? current.PULL_REQUESTS),
    COMMITS: Number(commitsRes?.data?.total_count ?? current.COMMITS),
    REPOSITORIES_CONTRIBUTED_TO: Number(current.REPOSITORIES_CONTRIBUTED_TO),
  }

  let template = fs.readFileSync(templatePath, 'utf8')
  template = template.replace(/\{\{\s*(ISSUES|PULL_REQUESTS|COMMITS|REPOSITORIES_CONTRIBUTED_TO)\s*\}\}/g, (_, key) => String(replacements[key]))
  fs.writeFileSync(readmePath, template)
}

module.exports = { run }

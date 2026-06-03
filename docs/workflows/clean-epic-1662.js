export const meta = {
  name: 'clean-epic-1662',
  description: 'Audit 22 child issues of epic #1662: verify status, detect hidden PRs, check dependencies, propose closures/updates',
  phases: [
    { title: 'Scan', detail: 'Fetch status of all 22 child issues' },
    { title: 'Verify', detail: 'Cross-check for hidden PRs, stale deps, implicit completions' },
    { title: 'Clean', detail: 'Apply batch updates (close, relabel, re-parent) to epic and children' },
  ],
}

const CHILDREN = [
  1636, 1637, 1634, 1660, 1663, 1664, 1665,
  1635, 1659, 1638, 1666, 1667, 1640, 1661,
  1639, 1652, 1653, 1654, 1655, 1656, 1657, 1658
]

phase('Scan')
const issues = await agent(
  `For each of these GitHub issue numbers: ${CHILDREN.join(', ')}, ` +
  `run \`gh issue view N --json number,title,state,labels,body,closedAt\` and ` +
  `\`gh pr list --search "closes #N OR fixes #N OR resolves #N" --json number,title,state,mergedAt\`. ` +
  `Produce a structured JSON report: {issues: [{number, title, state, labels, prs: [{number, state, mergedAt}]}]}.`,
  { label: 'scan-22-issues', phase: 'Scan', schema: {
    type: 'object',
    properties: {
      issues: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            number: { type: 'integer' },
            title: { type: 'string' },
            state: { type: 'string' },
            labels: { type: 'array', items: { type: 'string' } },
            prs: {
              type: 'array',
              items: {
                type: 'object',
                properties: {
                  number: { type: 'integer' },
                  state: { type: 'string' },
                  mergedAt: { type: 'string', nullable: true }
                }
              }
            }
          }
        }
      }
    }
  }}
)

log(`Scanned ${issues.issues.length} issues`)

phase('Verify')
const verify = await agent(
  `Given this epic #1662 with children: ${JSON.stringify(issues.issues)}, ` +
  `analyze: (1) which child issues have MERGED PRs but are still OPEN (should be closed), ` +
  `(2) which child issues have OPEN PRs (should be linked), ` +
  `(3) which issues are likely duplicates or superseded by recent work (check for keywords in titles), ` +
  `(4) dependency graph violations (e.g., child marked blocked-by but blocker is already closed). ` +
  `Return {proposals: [{issue, action: 'close'|'relabel'|'reparent'|'link-pr', reason, pr_number?}]}`,
  { label: 'verify-proposals', phase: 'Verify', schema: {
    type: 'object',
    properties: {
      proposals: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            issue: { type: 'integer' },
            action: { type: 'string', enum: ['close', 'relabel', 'reparent', 'link-pr', 'none'] },
            reason: { type: 'string' },
            pr_number: { type: 'integer', nullable: true }
          }
        }
      }
    }
  }}
)

log(`Proposed ${verify.proposals.length} clean actions`)

phase('Clean')
const applied = await agent(
  `Apply these proposals to GitHub issues: ${JSON.stringify(verify.proposals)}. ` +
  `For each 'close' action: run \`gh issue close {issue} --comment "Closing as part of epic #1662 clean — {reason}"\`. ` +
  `For each 'link-pr' action: run \`gh pr edit {pr_number} --body "closes #{issue}"\` if body doesn't already mention it. ` +
  `For 'relabel' or 'reparent': use \`gh issue edit {issue} --add-label ...\` or comment with reasoning. ` +
  `Return {applied: [{issue, action, success, output}], errors: [{issue, error}]}.`,
  { label: 'apply-clean', phase: 'Clean', schema: {
    type: 'object',
    properties: {
      applied: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            issue: { type: 'integer' },
            action: { type: 'string' },
            success: { type: 'boolean' },
            output: { type: 'string' }
          }
        }
      },
      errors: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            issue: { type: 'integer' },
            error: { type: 'string' }
          }
        }
      }
    }
  }}
)

log(`Applied ${applied.applied.length} actions, ${applied.errors.length} errors`)

return { scanned: issues.issues.length, proposals: verify.proposals, applied: applied.applied, errors: applied.errors }

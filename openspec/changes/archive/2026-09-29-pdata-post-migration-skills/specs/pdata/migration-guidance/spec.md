## REMOVED Requirements

### Requirement: The post-write prompts accept a dispatched subagent as a fresh-context substitute
**Reason**: The two post-write prompt files no longer exist; their procedures are now the skills
`pm-pdata-do-update-project-docs` and `pm-pdata-do-update-consuming-skills`.
**Migration**: The equivalent requirement ("Both skills run in a fresh context") lives in the
`pdata/post-migration-skills` capability.

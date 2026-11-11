# Does OpenSW cover your everyday work?

OpenSW aims to cover **at least 95% of a team's everyday needs** with a smaller feature set. This is a target for an individual team's fit assessment, not a measured statistic about SaaS users or a claim of complete feature parity.

Assess each app independently. A strong fit for Projects does not mean Chat will suit a team that depends on calls and threads.

## Use a weighted checklist

1. List the workflows your team actually performs during a representative month.
2. Assign each a weight based on frequency and importance. Use a consistent scale, such as 1–10.
3. Mark each workflow **fully supported**, **partially supported**, or **unsupported**, after trying it in the installed app.
4. For a transparent score, count fully supported workflows only. Discuss partial support separately rather than quietly counting it as complete.
5. Mark any must-have requirement. A single unsupported must-have can rule out a switch regardless of the average score.

**Fit score = sum of fully supported workflow weights ÷ sum of all workflow weights × 100.**

| Your workflow | Weight | Must-have? | Observed result | Evidence / gap |
| --- | ---: | --- | --- | --- |
| Example: upload a document and let a teammate download it | 10 | Yes | Test in Files | Record the actual result |
| Example: keep a folder synchronized automatically on every laptop | 8 | Yes | Unsupported in this release | Desktop sync is outside scope |
| Add your actual workflow |  |  |  |  |

The two example rows are not a score for OpenSW. They demonstrate why a familiar product category is insufficient: browser file sharing may fit your needs while automatic desktop synchronization is essential to another team.

## Start with the app's real scope

| App | Check these everyday workflows | Check these important omissions |
| --- | --- | --- |
| [Files](../apps/files/README.md) | Create folders; upload/download; search names; rename; share a member-only link; Trash/restore | Desktop sync, public links, version history, previews, permanent deletion |
| [Projects](../apps/projects/README.md) | Create projects/tasks; assign people; change status/priority; set dates; comment; archive/restore | Custom workflows, sprints, epics, reports, automations |
| [Notes](../apps/notes/README.md) | Write Markdown pages; preview; save; search titles; handle simultaneous-edit conflicts; archive/restore | Databases, formulas, nested page trees, live co-editing, public publishing |
| [Chat](../apps/chat/README.md) | Channels; private one-to-one messages; history/search; own-message edit/delete; unread state | Calls, threads, reactions, attachments, group DMs, mobile push |

Add non-feature requirements too: accessibility, device/browser support, acceptable response times, concurrent users, storage growth, recovery time, and who will maintain the installation. The example hosting budget in the main README is not a load-test result or a service guarantee.

## Try the switch before depending on it

Install one isolated app, create a teammate, and repeat your highest-weight workflows with realistic data. Run `./verify.sh --config path/to/your.local.toml`; its API checks establish specific behavior, not a percentage of your requirements. Use the optional browser checks and inspect the interface on your actual devices.

Test a backup and recovery procedure. Include archived/trashed content in capacity planning. Keep the existing system available while you assess migration; this release does not promise automated imports from the commercial products.

Finally, replace the main README's illustration with your invoices and your operating budget:

**Annual net saving = subscriptions actually avoided − infrastructure − administration − annualized setup/migration costs.**

Free subscriptions have no avoided subscription cost. A feature-fit percentage and a cost-saving percentage answer different questions; report them separately.

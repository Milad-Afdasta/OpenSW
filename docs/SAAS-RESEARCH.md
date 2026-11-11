# Ten widely used SaaS products: what can a team bring in-house?

Research checked **November 7, 2026**. The strongest initial candidates for a focused, AI-assisted replacement are **project boards, team documentation, browser file sharing, and internal text chat**. These are the four categories implemented by OpenSW. Replacing a vendor's complete platform is a substantially larger undertaking.

## Selection and evidence limits

This is a shortlist of ten major, widely used productivity and collaboration SaaS products, **not a verified global top-ten ranking**. Vendors report different populations and periods: registered accounts, all users, monthly or weekly activity, paid seats, customer organizations, and meeting participation. Those figures cannot be ranked as though they counted the same thing. SaaS here includes freemium applications and consumer use of business software; popularity does not mean everyone pays.

The figures below are dated primary-source disclosures located during this research. Older milestones establish historical reach, not current usage. Where a current product-wide user total was not established, the table says so rather than substituting an estimate. Row order is not a market-share ranking.

| Product | Published adoption evidence | What the number does and does not mean |
| --- | --- | --- |
| Google Workspace | **Over 3 billion users; over 13 million customers**, April 22, 2026. [Google announcement](https://workspace.google.com/blog/product-announcements/10-more-announcements-workspace-at-next-2026) | Broad suite reach, including consumer use; not three billion paid business seats. |
| Microsoft 365 | **Over 450 million paid commercial seats**, January 28, 2026. [Microsoft FY2026 Q2 call](https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q2) | Paid commercial seats; not a count of unique active people or all consumer users. |
| ChatGPT | **1.2 billion people reached each week**, October 5, 2026. [OpenAI announcement](https://openai.com/index/new-chatgpt-ads-format-and-measurement/) | Weekly reach reported by the company; not paying subscribers. |
| Dropbox | **Over 700 million registered users; 18.19 million paying users**, June 30, 2026. [Dropbox quarterly filing](https://investors.dropbox.com/node/14171/html) | Registered accounts are cumulative; Dropbox cautions that one person can register more than once. |
| Canva | **Over 265 million people each month**, stated in the 2026 Code 2.0 announcement. [Canva announcement](https://www.canva.com/newsroom/news/Canva-Code/) | Monthly usage, including free users. The announcement does not give a measurement cutoff in its readable text. |
| Zoom | **300 million daily meeting participants in April 2020**, reported in its FY2021 shareholder letter. [Zoom letter](https://investors.zoom.us/static-files/0229c042-e707-4b11-9110-6ea1e9ce31b4) | Historical participation, including repeat attendance by one person; not 300 million distinct daily users or a 2026 total. |
| Notion | **100 million users**, milestone reached August 2024, announced September 3, 2024. [Notion founder announcement](https://www.notion.com/blog/100-million-of-you) | Historical total users; the source does not define this as monthly activity or paid seats. |
| Slack | **Over 12 million daily active users in September 2019**, announced October 10, 2019. [Slack disclosure and definition](https://slack.com/blog/news/work-is-fueled-by-true-engagement) | Historical activity on free and paid plans. A current platform-wide active-user total was not established. |
| Figma | **Over 13 million monthly active users during Q1 2025**, disclosed in its July 2025 registration statement. [Figma SEC filing](https://www.sec.gov/Archives/edgar/data/1579878/000162828025036090/figmas-1a2.htm) | Activity across Figma products, including free users; not paid designer seats. |
| Jira | **Over 125,000 Jira customer organizations**, in Atlassian's Q1 FY2026 shareholder letter; its at-a-glance figures use the June 2025 fiscal-year baseline. [Atlassian letter, page 11](https://s206.q4cdn.com/270053503/files/doc_financials/2026/q1/TEAM-Q1-2026-Shareholder-Letter.pdf) | Organizations, not people. The same page's five-million-user sample combines Jira and Confluence; it cannot establish a Jira-only total. |

## Local AI-agent recreation scores

**10 = easiest; 1 = hardest.** These are comparative engineering judgments, not measured probabilities or promises of delivery time. They rate a **lean replacement for a small team's specified daily workflows**, using AI-assisted development and established libraries where appropriate, with human validation and ongoing maintenance. They do not rate rebuilding the vendor, serving millions of users, or matching every feature.

“In-house” means the replacement's application and retained data can run on machines you control without the original vendor's subscription. It does not mean installing the proprietary SaaS itself. Local runtime also does not prove that the AI agents used during development ran locally; cloud coding agents may still have fees. Fully local AI development additionally needs adequate hardware and a suitable local model.

| Product category | Lean recreation score | Can the scoped core run entirely in-house? | Practical boundary and hardest work |
| --- | ---: | --- | --- |
| Jira | **9/10** | **Yes:** projects, tasks, assignees, dates, comments, fixed board columns. | Custom workflows, sprints, reports, enterprise permissions, automation, and its marketplace are much larger scopes. |
| Notion | **9/10** | **Yes:** searchable documentation, Markdown pages, explicit saves, edit-conflict checks. | Rich block editing, relational databases, formulas, live co-editing, and reliable complex imports reduce feasibility. |
| Dropbox | **8/10** | **Yes:** authenticated browser uploads/downloads, folders, search, recoverable Trash. | Cross-platform background sync, offline conflicts, version history, recovery, and large-data migration are hard. Browser sharing alone does not replace desktop sync. |
| Slack | **8/10** | **Yes:** internal channels, private one-to-one messages, history, search, unread state. | Calls, mobile push, threads, external organizations, bots, compliance controls, and many integrations add substantial work. |
| Canva | **6/10** | **Yes, for selected layouts:** owned templates, text/image placement, export, basic brand settings. | The licensed asset/template library, polished editor, video tools, and AI media generation are expensive to reproduce. Existing stock-content rights do not transfer automatically. |
| Zoom | **5/10** | **Yes, for self-hosted web meetings** using an existing real-time communications stack. | Browser/network interoperability, relay bandwidth, recording, large calls, accessibility, and reliability are demanding. Phone numbers and calls to the public telephone network need external carrier services. |
| Figma | **5/10** | **Yes, for selected vector design and prototyping workflows.** | A polished collaborative canvas, layout engine, component system, developer handoff, and faithful file import are difficult to build from scratch. |
| Google Workspace | **4/10** | **Yes, as a collection of narrower services:** files, calendars, basic documents, and email. | Gmail delivery reputation, spam handling, office fidelity, live collaboration, identity, and administration create many operational owners. Internet email also depends on external DNS and recipient mail systems. |
| Microsoft 365 | **3/10** | **Yes, for basic office work** with alternative editors, storage, calendar, and mail services. | Advanced Excel behavior, macros, complex document fidelity, Teams, SharePoint, device management, and enterprise identity make full replacement impractical for a small build. |
| ChatGPT | **3/10** | **Yes, for a narrower local assistant** running a locally available model and local tools. | Building a chat interface is easy; reproducing frontier model quality, training, multimodal capabilities, current web access, and service capacity is not. Hardware, electricity, and model-license terms still matter. |

None of these scores establishes that complete current-vendor parity is realistic. Higher scores depend on deliberately excluding advanced features. Expanding the scope changes the score and the economics.

## Existing self-hosted software changes the build-versus-buy decision

Before commissioning a recreation, evaluate maintained software that already runs under your control. AI agents can help configure or customize it rather than generate another full application. These are alternatives to assess, not components included in OpenSW:

| Need | Primary-source self-hosted starting point | Limitation to assess |
| --- | --- | --- |
| Files and broader collaboration | [Nextcloud server installation](https://nextcloud.com/install/) | Deployment, storage, updates, backups, and the exact edition/apps remain your responsibility. |
| Project management | [OpenProject Community edition](https://www.openproject.org/download-and-installation/) | Free Community and paid Enterprise features differ. |
| Team chat | [Zulip self-hosting](https://zulip.com/self-hosting/) | Its conversation model differs from Slack; mobile delivery and integration requirements need separate assessment. |
| Web meetings | [Jitsi's official Docker deployment](https://github.com/jitsi/docker-jitsi-meet) | Size the media server and network for real concurrent calls; installation alone does not prove capacity. |
| Collaborative design | [Penpot self-hosting](https://help.penpot.app/technical-guide/getting-started/) | Evaluate existing design files and required workflows; self-hosting Penpot does not create Figma format/feature parity. |
| Mail | [mailcow documentation](https://docs.mailcow.email/) | Mail operation includes DNS, certificates, filtering, deliverability, maintenance, and recovery. |
| Local AI inference | [Ollama documentation](https://docs.ollama.com/faq) | Select a local model and disable cloud-dependent paths as needed; model capability, memory, concurrency, and licensing vary. |

There is no inherent need to put AI in the running product. Ordinary files, projects, notes, and chat can operate without inference costs. OpenSW uses that approach: AI assists development; the four runtime applications do not require a paid AI API.

## Why OpenSW starts with these four apps

The recommended initial scope concentrates on recurring team work with clear, bounded data and fewer external-service dependencies. Each app is independently installable, with its own accounts and storage. Current scope is documented in the linked app guides:

| OpenSW app | Included core | Deliberate omissions that may prevent a switch |
| --- | --- | --- |
| [Files](../apps/files/README.md) | Folders, upload/download, filename search, rename, member-only links, Trash/restore. | Desktop sync, public sharing, previews, version history. |
| [Projects](../apps/projects/README.md) | Projects, fixed three-column boards, tasks, assignees, priority/dates, comments, archive/restore. | Custom workflows, sprints, epics, reports, automations. |
| [Notes](../apps/notes/README.md) | Markdown pages, safe preview, explicit Save, title search, edit-conflict checks, archive/restore. | Databases, formulas, nested page trees, rich blocks, live co-editing. |
| [Chat](../apps/chat/README.md) | Channels, private one-to-one messages, history/search, own-message edit/delete, unread counts. | Calls, threads, reactions, attachments, group DMs, bots, mobile push. |

These are narrower alternatives, not complete vendor recreations. A target such as **95% of everyday needs** must be measured against the particular team's weighted workflows; this research establishes no universal usage or coverage percentage. Use the [feature-fit worksheet](FEATURE-FIT.md), and treat an unsupported must-have as a blocker even when most other work fits.

## Savings: compare total ownership costs

For an illustrative team buying **50 seats in each of the four selected products**, the following official US list prices were checked November 7, 2026. Tax, promotions, add-ons, negotiated discounts, and bundled plans are excluded.

| Subscription | Price per user/month | Billing basis | Annualized cost for 50 |
| --- | ---: | --- | ---: |
| [Dropbox Standard](https://www.dropbox.com/plans?trigger=homepagefoot) | $15.00 | Annual billing | $9,000 |
| [Jira Standard](https://www.atlassian.com/licensing/future-pricing/cloud/list/faqs) | $9.70 | Monthly billing, first 100 users | $5,820 |
| [Notion Plus](https://www.notion.com/pricing) | $10.00 | Annual billing | $6,000 |
| [Slack Pro](https://slack.com/pricing) | $7.25 | Annual billing | $4,350 |
| **Gross subscription spending** | **$41.95** | Mixed billing as stated | **$25,170/year** |

This illustrates a potential spending pool, not equivalent features, storage, service guarantees, or guaranteed savings. Free plans create no subscription saving, and small teams may find a subscription cheaper than maintaining software. Replacing only one application avoids only that application's actual invoice.

**Annual net saving = subscriptions actually avoided − infrastructure − administration − annualized setup and migration costs.** Include hardware depreciation, electricity, storage growth, off-server backups, recovery testing, monitoring, updates, support, and any remaining external services. Local hosting moves these responsibilities to the operator; it does not remove their cost. See the [README's explicit hosting assumptions](../README.md#what-could-a-50-person-team-save) for a worked illustration, which is not a capacity benchmark.

The practical recommendation is to trial **one** of Files, Projects, Notes, or Chat against real workflows and a tested restore before canceling the paid service. Use a maintained self-hosted product where it already meets the need; build a narrower original app where its smaller scope has clear value. Retain commercial tools for required functionality whose replacement costs exceed the fees avoided.

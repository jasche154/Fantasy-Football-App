You are working exclusively on my fantasy football application.

GENERAL RULES
- Understand the existing implementation before making changes.
- Make the smallest correct change necessary.
- Do not refactor unrelated code.
- Do not add libraries unless they are actually necessary.
- Follow the existing project structure and coding style.
- Do not invent APIs, database fields, player statistics, scoring rules, or data sources.
- Preserve existing functionality unless I specifically ask you to change it.

FANTASY FOOTBALL LOGIC
- Treat scoring, rankings, projections, player data, teams, positions, weeks, and seasons as important business logic.
- Do not assume fantasy scoring rules. Inspect the existing implementation first.
- Be careful with player IDs, team names, positions, weeks, seasons, and dates.
- Distinguish between actual statistics, projections, rankings, and recommendations.

AUTONOMOUS WORKFLOW
For every coding task:
1. Inspect the relevant files and understand how the existing implementation works.
2. Briefly determine the plan.
3. Make the requested change.
4. Run the relevant tests, build, type checks, lint checks, or other available verification.
5. Inspect the files you changed for mistakes.
6. Test the feature again when possible.
7. If you find an error, fix it and run the relevant checks again.
8. Do not consider the task finished simply because the code was written.
9. At the end, briefly report:
   - What you changed
   - What files you changed
   - What checks/tests you ran
   - Whether anything remains unresolved

SELF-CHECK REQUIREMENT
Before declaring any task complete, review your own changes and verify that they work.
For important logic changes, test with concrete examples or known expected results.
For frontend changes, verify that the application still builds and that the affected functionality works.
For backend/API changes, verify the affected endpoint or logic when possible.

SAFETY / SCOPE
- Work only within this fantasy football project.
- Do not modify unrelated projects or personal files.
- Do not delete files unless the task requires it.
- Do not modify credentials, API keys, or environment secrets unless I explicitly ask.
- Do not make unrelated cleanup changes.
- If a requested change requires modifying something outside the project directory, stop and ask me first.
## Future Work: Computer Vision-Based Running Back Vision Score

A potential future project is to develop a **Running Back Vision Score** that evaluates a runner’s ability to recognize running lanes, demonstrate patience, synchronize with blockers, and adjust to changes in the play. The goal is to measure aspects of running back performance that traditional rushing statistics may not fully capture.

### Proposed Metrics

The Vision Score would evaluate four main areas:

- **Hole Recognition:** Evaluates whether a runner identifies and chooses an effective running lane based on the defensive alignment, available space, and developing blocks.
- **Patience and Timing:** Measures whether a runner allows blocks to develop before accelerating through a lane. This includes recognizing when to wait, when to attack, and when waiting too long causes an opportunity to close.
- **Blocking Synchronization:** Evaluates how well a runner coordinates their movement with offensive linemen and other blockers, including whether they follow the intended blocking scheme and time their cuts appropriately.
- **Adaptability:** Measures how effectively a runner responds when the expected lane closes, a defender penetrates the line, or a blocker misses an assignment. This includes identifying alternative paths and making effective adjustments.

Each component could receive an individual grade before being combined into an overall score. Computer vision could help automate this process by detecting and tracking players, analyzing movement and spacing, identifying potential running lanes, and measuring how a runner moves in relation to blockers and defenders.

### Integration With Existing Rushing Statistics

The Vision Score would supplement existing metrics rather than replace them. Statistics such as Rushing Yards Over Expected (RYOE) per attempt, yards after contact, missed tackles forced, and success rate measure different aspects of rushing performance. However, these statistics primarily describe the results of a play or compare those results against a benchmark. The Vision Score would focus on the runner's decisions and movement throughout the play.

For example, two running backs could average similar RYOE per attempt but achieve those results in different ways. One runner might consistently identify the correct lane, wait for blocks to develop, and make efficient decisions. Another might rely more heavily on speed, broken tackles, or occasional explosive runs. RYOE alone may not fully distinguish between these approaches.

Combining RYOE per attempt with the Vision Score could help analysts determine whether a runner's production is supported by consistent decision-making. It could also identify runners who make good decisions but produce limited yardage because of defensive pressure, ineffective blocking, or other factors outside their control. Conversely, it could help identify runners whose production looks strong statistically but whose decisions are less consistent.

The two measures would provide complementary information:
- **RYOE per attempt:** How much rushing yardage a player generates above or below expectations.
- **Vision Score:** How effectively a player recognizes opportunities and makes decisions while the play develops.

Together, these metrics could provide a more complete evaluation of running back performance, helping analysts distinguish between decision-making, physical ability, blocking support, and final outcomes.

### Proposed Computer Vision System

The system could use video footage to detect and track the running back, offensive blockers, and defenders throughout each rushing play. From these tracks, it could estimate player movement, acceleration, spacing, changes in direction, and the timing of interactions between the runner and blockers. Additional analysis could identify potential running lanes and estimate how those lanes change as defenders move.

The extracted information would feed into a scoring model that evaluates the four proposed components of the Vision Score. Each play could receive an overall grade alongside individual component scores, allowing analysts to identify specific strengths and weaknesses rather than relying on a single number.

The system would also include a human-review process. Clips with obstructed views, tracking errors, uncertain player identities, ambiguous running lanes, or unclear blocking assignments would be flagged for manual evaluation. Reviewed clips could be used to correct errors, refine grading criteria, and improve the model over time. The system should distinguish between a genuinely poor decision and a play where the available video does not provide enough information to make a reliable judgment.

### Example: Play-Level Vision Score

Consider a running play where the running back receives a handoff and approaches the line of scrimmage. The offensive line begins opening a lane on the right, but a defender starts closing the gap. The running back briefly waits for the block to develop, recognizes that the right-side lane is closing, and cuts through an opening on the left.

The play gains only three yards because a second defender makes the tackle shortly after the runner crosses the line of scrimmage. Although the statistical outcome is modest, the runner demonstrates several positive decisions.

The play could be graded using the following rubric:

| Component | Grade (0–4) | Evaluation |
|---|---:|---|
| Hole Recognition | 4 | Identifies the developing left-side opening after the initial lane begins closing. |
| Patience and Timing | 3 | Allows the block to develop without waiting so long that the opportunity disappears. |
| Blocking Synchronization | 3 | Times the cut reasonably well in relation to the blockers, although the execution is not perfect. |
| Adaptability | 4 | Recognizes the defensive adjustment and switches to an alternative lane. |

For an illustrative scoring model, the four components would have the following weights: Hole Recognition (35%), Patience and Timing (25%), Blocking Synchronization (25%), and Adaptability (15%).

Each component grade would be divided by four and multiplied by its weight. The weighted scores would then be added together:

\[
\begin{aligned}
\text{Vision Score} ={}&
(4/4 \times 35)\\
&+(3/4 \times 25)\\
&+(3/4 \times 25)\\
&+(4/4 \times 15)\\
={}& 87.5/100
\end{aligned}
\]

The resulting **Vision Score is 87.5 out of 100**, indicating strong decision-making on this particular play under the proposed rubric.

The score would be recorded separately from the play's actual yardage and RYOE. If the runner gained three yards but had positive RYOE, the two measures would describe different aspects of the play: RYOE would quantify the rushing result relative to expectations, while the Vision Score would evaluate the runner's decisions.

This example also illustrates why the Vision Score should not automatically reward successful outcomes or penalize unsuccessful ones. A runner could make a sound decision and still be tackled for a short gain, while another could make a questionable decision and gain substantial yardage because of speed or a defensive mistake. The grading criteria would need to focus on the quality of the decision given the information available at that moment.

The component grades and weights are illustrative and would need to be refined and validated using real game footage and independent human evaluations.

### Data Collection and Validation

Future development would require a sufficiently large collection of suitable game footage, reliable player tracking, and consistently labeled examples of running back decisions. Human reviewers would establish grading criteria and evaluate a sample of plays to create reference scores. Multiple reviewers could grade a subset of the same plays to measure consistency.

The Vision Score would then be tested against existing rushing statistics, including RYOE per attempt, missed tackles forced, yards after contact, explosive run rate, and success rate. An important objective would be to determine whether the Vision Score provides useful information beyond these existing metrics. For example, analysts could test whether it helps explain differences in rushing performance or improves predictions of future performance when added to a model that already includes traditional statistics.

Because suitable footage and labeled data are not currently available in sufficient quantities, the immediate priority would be to identify appropriate data sources and establish a repeatable grading framework before developing a reliable automated model.
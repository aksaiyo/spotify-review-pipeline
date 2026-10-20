# Decision Memo - Spotify Product Priority

**Recommendation:** Prioritize usability next quarter, focusing on ads and restoring reliable song choice, skipping, rewind, and playlist controls. usability-ads has the largest listed complaint_count and a substantial severity_sum; its examples describe ads alongside impaired controls. Treat this as a focused usability recommendation, not evidence that ads alone cause the issue.

**Priority issue:** usability-ads

**Alternatives considered:**
- Compared with billing-premium, which has a higher mean_severity but fewer complaints, usability-ads has complaint_count 3809 versus 2230 and severity_sum 9525 versus 6192. Billing/support remains a strong alternative: the billing-premium examples include repeated charges, failed upgrades, and difficulty reaching support.
- Compared with playback-general, usability-ads has complaint_count 3809 versus 891 and severity_sum 9525 versus 2750; playback-general has higher mean_severity, 3.08642 versus 2.500656. Playback is therefore a plausible alternative if effort is aimed at the most severe playback complaints rather than broader complaint volume.
- Access is not separately represented among the listed issue IDs, so this input does not support a direct access comparison.

**Claims (validated against saved calculations):**
- usability-ads complaint_count = 3809
- usability-ads severity_sum = 9525
- usability-ads mean_severity = 2.500656
- billing-premium complaint_count = 2230
- billing-premium severity_sum = 6192
- billing-premium mean_severity = 2.776682
- playback-general complaint_count = 891
- playback-general severity_sum = 2750
- playback-general mean_severity = 3.08642

**Evidence review IDs:** 02ebf89c-b923-44b8-92d1-972df45c4ee7, 030925d2-b75b-4585-a2ee-c17d7a941087, 05bc7cc7-9a80-414e-82d3-4446cf1cd43b, 04af8ea4-e452-444e-9612-30c5ce8081dc, 0da2b195-bce2-4e77-9ac0-6f1f66448e8f, 0340ab24-d089-4a78-9886-1db2c2866aab

**Limitations:**
- Reviews are self-selected and may not represent Spotify users overall.
- The aggregates cover a historical window; they may not reflect current product conditions.
- Intent is expressed in reviews, not observed churn.
- No revenue data is provided.
- The evidence pack is bounded and the listed categories do not provide a distinct access issue for direct comparison.
- The input provides no priority_score values, so recommendation comparisons use only complaint_count, severity_sum, and mean_severity.
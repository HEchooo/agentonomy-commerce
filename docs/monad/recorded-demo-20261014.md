# Recorded demo — 2026-10-14

This note records the English technical demo take made against the public
Agentonomy Commerce review deployment. It is separate from the
[micro-price preflight](public-micro-price-acceptance-20261014.md), whose
assessment evidence is different.

## Recorded flow

- Network: Monad testnet; TestUSD has no monetary value.
- Service price: `0.001 TestUSD`.
- Purchase: the recorded order entered a pending state, then recovered through
  the same order and reached delivered status. No replacement purchase was
  created.
- Delivery: the report matched USD `255.00` income, `40.50` expense, and
  `214.50` net.
- Reopen: reopening the report returned the existing result without another
  charge.
- Consumer-Agent assessment: task `100`, result `100`, interaction `80`,
  weighted `96/100`, confidence `medium`. This is an off-chain Agent opinion,
  not an on-chain score or an independently verified quality claim.
- Buyer feedback: optional and wallet-signed when requested; no manual buyer
  feedback was included in this recording.

The raw take was completed on October 14. Revocation/refusal and second-wallet
isolation were not recorded.

## Published films

- [Technical demo](https://review.agentonomy.xyz/videos/technical-demo.html):
  128.8667 seconds, 1920×1080, Alba English narration and English captions.
- [Product pitch](https://review.agentonomy.xyz/videos/pitch.html):
  85.5 seconds, explaining the team motivation and marketplace direction.

The technical edit shortens waiting time and preserves original playback speed.
The source recording uses variable frame timing; a constant-frame-rate
intermediate preserves elapsed time before trimming. The final audio and video
durations agree within one frame. Spoken amounts and assessment values were
checked against the narration, and the final composition passed a full decode
and visual review. Narration was generated after the recorded session.

The pitch is an illustrated product introduction, not payment evidence. The
public video pages include the synthetic narration source and license credits.

## Submission status

Both film links were submitted to Monad Metropolis on 2026-10-14 in the
Trust, Identity & AI Infrastructure track. The submission page confirmed
"Ready for judging". This status records submission, not a judging result.

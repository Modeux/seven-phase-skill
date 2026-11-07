# Probe A — code-explorer without the turn rule (baseline)

agent ad9369618ab577d4b · 64 tool uses · 200 s · 190k subagent tokens · 33 API messages · $2.04 · 1.94 calls/msg.
Same brief as B minus the turn rule. B: 52 tool uses · 167 s · 18 messages · $1.68 · 2.89 calls/msg.

## Completeness vs B
Both reports reach the same chain: `chatUnlockView.gatePost` (service.go L1810) as the single setter of
`media_gate="chat"` + `gated_preview_url`; `withholds` (L1771-1783); unlock set = active `conversations`
rows (`ListInteractionUnlockedCharacterIDs`) via `wire_viewer_access.go` L43 / `wire_workers.go` L3966;
`includeNsfw` L1701, detail 404 L1861, favorites L2725-2744, video parent L1980-1983; frontend mounts on
`media_gate === 'chat'` at gallery card / character-page public card / favorites / lightbox stage; chat room
mounts neither; `MediaGatePreview` also needs `contentAdult()`; unlock = `ChatUnlock.startChat` →
`findOrCreateByCharacter`, `create` bumps `ChatUnlockVersion`, gallery / lightbox / favorites re-read; the
character page works key has no unlock version (open question in both).

A additionally read: `work-detail.buttons.ts` L222-227 (`unlock` button), `work-detail-actions.ts` L294-297,
`work-detail.cards.ts` L89／L113／L216, `community/image_gate.go` (separate `image_gated`), the sqlc-generated
`community.sql.go` L3260-3266 (B read `db/queries/community.sql` L662-667 instead), `borrow.go` L335-461.
B additionally listed `core/data/gallery.ts` L86 (`getPublic`) and `handler.go` L621 (`ListFavorites` entry).
Neither missed a file the other needed for the decision chain; A's extras are peripheral (action row, cards,
a sibling mechanism). Verdict for the probe: completeness equal; B cheaper and faster.

## Open questions A raised (not in B)
- gallery store keys on `persistedStatus`, not the account id (account switch in another tab) — outside the gate question.
- `urlutil.WebPThumb160` fail-closed behaviour not verified in code.

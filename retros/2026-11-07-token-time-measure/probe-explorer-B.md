# Probe B — code-explorer with the turn rule (batched reads)

agent a17ee2d6ae6fef61e · 52 tool uses · 167 s · 186k subagent tokens · same brief as A plus the turn rule.

## Files (as reported)
- backend-golang/internal/derivative/service.go — L1674-1741 GalleryList／galleryListLatest／Popular／galleryPostsFor (includeNsfw L1701, gatePost L1737); L1754-1795 chatUnlockView／newChatUnlockView／withholds／read; L1810-1819 gatePost (only setter of GatedPreviewURL + MediaGate="chat"); L1827-1836 gatedPreviewSource; L1844-1884 GalleryDetail (404 at L1861, gatePost L1876-1877); L1955-1985 galleryVideoParent (parent MediaGate L1980-1983); L2033-2043 galleryPostFromEntry (SourceCharacter rule); L2725-2746 ListFavorites; L371／L546 ChatUnlockReader + WithChatUnlockReader
- backend-golang/internal/derivative/model.go — L110 mediaGateChat; L538-545 GalleryPost.MediaGate／GatedPreviewURL
- backend-golang/internal/derivative/handler.go — L532／549 GalleryList, L570-571 GalleryDetail, L621 ListFavorites
- backend-golang/cmd/server/wire_workers.go — L3966 WithChatUnlockReader(newViewerAccessReader(pool))
- backend-golang/cmd/server/wire_viewer_access.go — L29-48 newViewerAccessReader／UnlockedCharacterIDs
- db/queries/community.sql — L662-667 ListInteractionUnlockedCharacterIDs (`is_active=TRUE` = "has chatted")
- docs/api-contracts/openapi.yaml — L24374-24389 MediaGate enum; L35552-35565 GalleryPost.media_gate／gated_preview_url; route text L9643／9843／10038; descriptions L8635／8751／9656／9866／10061
- angular-workspace/.../core/api.types.ts L2022; shared/media-gate-preview/media-gate-preview.ts L34-57; shared/media-gate-mask/media-gate-mask.ts L38-72; core/chat-unlock.ts L13-38; core/chat-unlock-version.ts L10-17; core/data/conversations.ts L60-79; features/gallery/gallery-browse.html L152-190; gallery-browse.ts L390-394; gallery-browse.store.ts L217-221／L242-256; gallery-viewer-key.ts L14-32; features/character-detail/works/character-works.models.ts L94-98／L267-284; character-works-grid.ts L71-73／L117-138; character-works-grid.html L6-26; character-works.store.ts L43-52／L362-379; character-detail.ts L189-199 + character-detail.html L390-391; features/my-derivatives/my-derivatives.html L65-91; my-derivatives.store.ts L308-314／L386-399; shared/work-detail/work-detail.adapters.ts L79-82／L103-118／L175／L228; work-detail.html L71-87; work-detail.reader.ts L133-148; core/data/gallery.ts L46-57／L86; features/chat-workspace/chat-workspace.ts L2234-2250／L2821 + chat-workspace.store.ts L6876-6878
- backend-golang/internal/community/decision.go L127 DecideStory — not relevant (story LLM decision)
- Also found by grep, not in seeds: borrow.go L335／346／399／461 (second user of newChatUnlockView)

## Decision chain (summary)
1. unlock set = active `conversations` rows for the viewer (community.sql:662-667) via viewerAccessReader (wire_viewer_access.go:43; wiring wire_workers.go:3966)
2. 18+ visibility: list `includeNsfw = PublicNSFWEnabled() && viewerIsAdult` (service.go:1701); detail 404 (L1861); favorites `favoritesIncludeNsfw` (L2730)
3. per-row mask: `gatePost` (L1810) ← `withholds` (L1771-1783): viewer "" → withhold; author → show; else withhold unless source in unlock set; sets gated_preview_url (only if viewer != "" and SourceCharacter != nil), clears asset_url／poster_url／scene, MediaGate="chat"
4. wire: `media_gate` + `gated_preview_url`; GalleryPostDetail／FavoritePost embed GalleryPost; video parent.media_gate set separately (L1980-1983), no preview
5–8. gallery card／character-page public card／favorites／lightbox each mount MediaGatePreview + MediaGateMask on `media_gate === 'chat'`; character page mask uses the page character, others `post.source_character?.id`
9. MediaGatePreview additionally needs `AgeGateStore.contentAdult()` and not failed (media-gate-preview.ts:51); mask checks only characterId
10. unlock: mask → ChatUnlock.startChat → findOrCreateByCharacter → navigate; `create` bumps ChatUnlockVersion (conversations.ts:63); gallery／lightbox／favorites re-read on the bump; chat room never mounts these components

## Mount points
| Surface | Component | Gating input |
|---|---|---|
| Gallery card (gallery-browse.html:152-188) | both | `post.media_gate === 'chat'`; Preview `post.gated_preview_url`; Mask `post.source_character?.id ?? null` |
| Character page public card (character-works-grid.html:6-25) | both | `worksCardFace(...).face.kind === 'chat_mask'` ⇐ `media_gate === 'chat'`; Mask `characterId()` = page character |
| Saved／favorites card (my-derivatives.html:65-81) | both | `post.media_gate === 'chat'` |
| Lightbox main stage (work-detail.html:73-79) | both | VM `work.mediaGate === 'chat'` (gateOf); Mask `work.character?.id ?? null` |
| Lightbox parent／second-ref card | neither | server sets parent.media_gate, no preview (work-detail.cards.ts not read) |
| Chat room | neither | character `!!media_gate` → neutral placeholder |

## Open questions (as reported)
1. character-page public cards do not re-read on unlock (`worksViewerKey` has no unlockVersion; today startChat navigates away so the section remounts)
2. finding an existing but inactive conversation does not bump the version → mask could stay after chatting again
3. character-page mask uses page character, not `post.source_character`
4. client gates the picture on `contentAdult()` while server gates on persisted `adult_status` (client only tightens)
5. work-detail.cards.ts not read

## Seed notes (as reported)
DecideStory not relevant; the real gate `gatePost` was not seeded. openapi seed line numbers do not match the literal text (relevant text at L8635／8751／9866／10061, schemas L24374／L35552-35565). "18+ room gate" (L10983／L12193) is about chat routes, not this flow.

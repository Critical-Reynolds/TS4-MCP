# Build-mode discovery spike (game 1.128.90.1030, 2026-10-07)

Question: can walls, floors, roofs, foundations, stairs, pools, fences or terrain be created from the
game's embedded Python?

## Method
- Decompiled the installed game with Motherlode (99.93% of code objects verified) into `reference/`.
- Read the native-module stubs Motherlode generates for engine modules compiled into `TS4_x64.exe`
  (`reference/stubs/_buildbuy.pyi`, `_lot.pyi`, `_areaops.pyi`, `_terrain.pyi`, `_placement.pyi`,
  `_zone.pyi`).
- Grepped every script for wall/floor/roof/blueprint/architecture mutation calls, listed the
  distributor operations the server can push to the client (`distributor/ops.py`,
  `DistributorOps_pb2`), and listed the registered console commands (`server_commands/`).
- Confirmed in the live game that `_buildbuy` exposes the same ~90 names (via `run_python`).

## Result: no write path for architecture
`_buildbuy` natives that touch geometry are all **read-only**: `get_wall_contours`,
`get_all_block_polygons`, `get_block_id`, `get_room_id`, `has_floor_at_location`,
`is_location_outside`, `is_location_natural_ground`, `is_location_pool`, `get_pool_edges`,
`get_pool_polys`, `get_pond_*`, `get_stair_count`, `get_plex_outline`, `get_lowest/highest_level_allowed`.
The only mutators are floor *features* (`set_floor_feature`, burnt floor marks used by the fire
service), household-inventory moves, venue owner, lot decorations and plex visibility.

Build mode itself runs in the C++ client. Python only receives callbacks when it begins/ends
(`c_api_buildbuy_session_begin/end`, `c_api_on_apply_blueprint_lot_begin/end`,
`c_api_wall_contour_update`, `c_api_navmesh_update`). The server→client operation set has
`SetWallsUpOrDown`, `OverrideWallsUp`, `BuildBuyLockUnlock`, `SetBuildBuyUseFlags`,
`SetLotDecorations`, `FocusCameraOnLot` and `MoveHouseholdIntoLotFromGallery`, but nothing that
places or edits architecture. No console command places walls, rooms, roofs or blueprints
(`bb.*` commands only toggle flags or force-exit build mode).

## What *is* scriptable (and implemented)
- Buying, placing, moving, rotating, selling any catalog object (`objects.*` ops), with the game's own
  legality test (`build_buy.test_location_for_object`).
- Full catalog index with English names, prices and tags (`catalog.*` ops + `catalog_search` tool).
- Lot geometry for planning: bounds, levels, block polygons, wall contours, pools, terrain heights,
  per-point probes (`lot.layout`, `lot.probe`).
- Buying/moving house: `household.move_into_zone` command exists and is wired (`move_household_to_lot`).

## Fallback adopted (agreed with the user)
"Furnish-only": the agent designs the floor plan and asks the player to place a Gallery shell or draw
the walls; the agent then does doors, windows, fixtures, furniture, landscaping and everything else.

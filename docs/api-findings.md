# Game API findings (confirmed on 1.128.90.1030)

Confirmed live via `run_python` unless marked "reference only".

## Runtime
- Embedded Python 3.7.0 (`sys.version` in game). Extension modules present: `_socket`, `select`,
  `_ctypes`, `_queue`, `_decimal`, `pyexpat`, `unicodedata`. Absent: `_ssl`, `_sqlite3`, `_hashlib`.
- Script mods load from `.ts4script` zips of `.pyc` compiled with CPython 3.7 (we compile with the
  3.7.9 embeddable distribution in `.toolchain/py37`). `Options.ini` needs `scriptmodsenabled = 1`.
- `game_services.on_tick` runs every server tick (main menu included) and is a safe pump hook.
  `Zone.update` only runs with a zone instantiated. `areaserver.c_api_server_tick` is the caller.
- Background thread + `queue.Queue` + tick pump works; the thread never touches game objects.
- Hot reload: executing source into live module objects (`bridge.reload` with `src_dir`) works for
  ops/helpers. Core transport/pump/dispatch modules are not reloaded (they hold live state).

## Services and lookups
- `services.current_zone()` / `_zone_state` (`ZoneState.RUNNING` when a lot is live; `is_zone_running`
  is a property, not a method).
- `services.client_manager().get_first_client()`; `client.id` is the `_connection` for commands.
- `services.active_sim_info()`, `services.active_household()`, `household.funds.money`,
  `household.sim_info_gen()`, `household.home_zone_id`.
- `services.get_persistence_service().zone_proto_buffs_gen()` → `ZoneData` protos with `zone_id, name,
  world_id, lot_id, household_id, lot_description_id, bedroom_count, bathroom_count, lot_traits, ...`
- `services.venue_service().get_venue_tuning(zone_id)` → venue class (`venue_residential`), `.is_residential`.
- `services.get_instance_manager(Types.INTERACTION|TRAIT|CAREER|STATISTIC).get(id)`; `.types.values()` to enumerate.

## Sims
- Names: `sim_info.first_name` / `last_name` work; `full_name` returned '' for a CAS-made sim.
- Age/gender are enums with `.name`; `species` is a plain int → map through `sims.sim_info_types.Species`.
- Motives: `sim_info.commodity_tracker.get_all_commodities()` filtered by `is_visible` gives
  `motive_Hunger`, `motive_Energy`, `motive_Bladder`, `motive_Fun`, `motive_Social`, `motive_Hygiene`
  (range -100..100).
- Skills: `sim_info.all_skills()`; each has `stat_type`, `get_user_value()`.
- Buffs: iterate `sim_info.Buffs`; `buff.buff_type`, `.mood_type`, `.buff_name` (often hash 0 = hidden).
- Traits: `sim_info.trait_tracker.equipped_traits`; `.display_name`, `.trait_type`.
- Careers: `sim_info.career_tracker.careers` dict; `career.level`, `user_level`, `current_level_tuning.title`,
  `get_hourly_pay()`, `currently_at_work`, `is_work_time`, `get_next_work_time()`.
- Relationships: iterate `sim_info.relationship_tracker`; `get_relationship_score(other_id)`, `get_all_bits(other_id)`.

## Interactions
- Enumerate: `target.potential_interactions(InteractionContext(sim, SOURCE_PIE_MENU, Priority.High))`
  yields `AffordanceObjectPair`s; `aop.test(context)` gives runnability; `aff.get_name(target=, context=)`
  gives the LocalizedString (resolved server-side from the game's STBL files).
- Push: `AffordanceObjectPair(aff, target, aff, None).test_and_execute(context)`; result truthy with
  `.interaction.id`. `aff.is_super` is a property.
- Queue: `sim.queue` (iterable), `sim.si_state` (running SIs); `interaction.cancel(FinishingType.USER_CANCEL, msg)`.
- Idle affordances (`sim-stand`, `stand_Passive`) spam the interaction events and are filtered.

## Time
- `services.game_clock_service().set_clock_speed(ClockSpeedMode.X)`; `clock_speed`.
- `services.time_service().sim_now` → `DateAndTime` with `hour()`, `minute()`, `day()`,
  `absolute_minutes()`, `absolute_days()`. Ticks are not 25/min; use `absolute_minutes()`.

## Events / dialogs / save
- `services.get_event_manager().register(handler, [TestEvent...])`, handler has
  `handle_event(sim_info, event_type, resolver)`. Events can arrive several times per occurrence → dedupe.
- `services.ui_dialog_service()._active_dialogs`; hook `UiDialogService.dialog_show/dialog_respond/
  _dialog_cancel_internal`; answer with `dialog_respond(dialog_id, response_id, client)` or
  `dialog_pick_result(dialog_id, picked_results=[...])`.
- Save: live command `persistence.save_game` (also `save_to_new_slot`, `save_game_with_autosave`);
  `PersistenceService.is_save_locked()`; hooks on `save_using` / `_destroy_save_timeline` give start/done.

## Objects / buy mode
- `objects.system.create_object(def_id)`; `obj.location = Location(Transform(Vector3, quat), SurfaceIdentifier)`;
  `sims4.math.angle_to_yaw_quaternion(radians)`; `obj.set_household_owner_id(id)`; `obj.destroy(source=, cause=)`.
- Legality: `build_buy.test_location_for_object(obj, location=loc)` or `(None, def_id, loc, [])` → `(ok, [(code, msg)])`.
- Ground: `services.terrain_service.terrain_object().get_routing_surface_height_at(x, z, surface)`.
- Catalog: `sims4.resources.list(type=Types.OBJECTDEFINITION)` (20,193 keys on this install);
  `build_buy.get_object_catalog_name(def_id)` → STBL hash (0 for non-catalog), `get_object_all_tags`,
  `definition_manager().get(def_id, pack_safe=True)` → `.price`, `.cls`.
- Funds: `household.funds.try_remove(price, Consts_pb2.TELEMETRY_OBJECT_BUY)` / `.add(v, TELEMETRY_OBJECT_SELL)`.

## Household / world (reference only so far)
- `household.move_into_zone(zone_id)` and live command `household.move_into_zone <zone> <hh> <furnished>`.
- `sim_info.send_travel_switch_to_zone_op(zone_id=)`, `sim_info.inject_into_inactive_zone(zone_id)`.
- `SimCreator(gender=, age=, species=, first_name=, last_name=, traits=())` +
  `SimSpawner.create_sim_infos((creator,), household=, generate_deterministic_sim=True, creation_source=)`,
  `SimSpawner.spawn_sim(sim_info, sim_position=)`.

## Socials (confirmed live)
- The pie menu toward a sim is built from `target.potential_interactions(context)` **with a PickInfo**
  (`server.pick_info.PickInfo(pick_type=PickType.PICK_SIM, target=, location=, routing_surface=)`) via
  `client.create_interaction_context(sim, pick=pick)`. Without the pick, `sim_Chat` and template
  affordances are missing (78 vs 161 AOPs).
- Social categories/mixers come from `aop.affordance.potential_pie_menu_sub_interactions_gen(target,
  context, None, **aop.interaction_parameters)` for each super AOP (yields `(mixer_aop, test_result)`),
  plus `autonomy.content_sets.generate_content_set(sim, si.super_affordance, si, context,
  potential_targets=(target,))` for running SIs. Toward a stranger only the introduction mixers show
  (`mixer_social_FriendlyIntroduction_greetings` etc.), exactly like the UI.
- `careers.add_career` is a Cheat-type command (needs `testingcheats true`); joining via
  `career_tracker.add_career(career_cls(sim_info))` works directly.
- Cheat-type console commands are silently ignored unless `testingcheats true` has been executed on the
  connection; `run_cheat` now does that automatically.

## Furnishing (confirmed live while furnishing Beech Byway)
- Placement yaw: `angle_to_yaw_quaternion(deg)`; 0 faces +z, 90 +x, 180 -z, 270 -x. Wall-backed items face
  into the room with rot = direction away from the wall (right wall at +x -> 270).
- Slot-only items fail free placement with `RequiresSlotForPlacement` (counter sinks) or
  `CantIntersectOtherObjects` (dining/desk chairs facing a table). Place them with
  `parent.get_runtime_slots_gen()` -> `slot.is_valid_for_placement(obj=child)` -> `slot.add_child(child)`,
  then `set_household_owner_id` and `household.funds.try_remove(price, Consts_pb2.TELEMETRY_OBJECT_BUY, sim_info)`.
  Matching by `child.slot_type_set` covers counter sinks (`_ctnm_applianceSurface_`), chairs (`_ctnm_chr_N`),
  computers/TVs (surface slots); deco slots (`_deco_lrg`, lamps) don't share types, so fall back to
  testing every empty slot.
- Ceiling objects (lights, hoods) need `y` at the ceiling: `MustBeOnCeiling` unless y is in about
  [floor+2.85, floor+3.0] for a standard wall. Wall objects (smoke alarm, windows) need `MustBeAgainstWall`
  positions: windows on the wall centerline, alarm on the interior face. `buy_object`/`test_placement`/
  `move_object` take `height` (metres above floor); when omitted and the game says MustBeOnCeiling, the
  bridge scans 2.0-6.5 m and keeps the top of the valid band (flush with the ceiling).
- Slot placement is exposed as `object_slots` + `buy_into_slot` (ops `objects.slots`, `objects.buy_into_slot`).
- MCP SDK 2.3 masks the text of any exception that isn't `ToolError` ("Error executing tool X" only), so
  `bridge_call` must raise `ToolError` for bridge errors to reach the model.
- Windows placed this way pass the legality test and are charged. The visual wall cutout was not yet verified.

"""Native Java 1.21.10 travel controls and a WGS84 city/coordinate list."""
import json
from pathlib import Path

import nbtlib as n

from city_catalog import DEFAULT_PATH, load as load_catalog, materialized


def install_controls(world, city_catalog=DEFAULT_PATH):
    world = Path(world)
    pack = world / 'datapacks/earthcraft_travel'
    if pack.exists():
        raise FileExistsError(pack)
    functions = pack / 'data/earthcraft/function/travel'
    functions.mkdir(parents=True)

    def function(name, lines):
        (functions / f'{name}.mcfunction').write_text('\n'.join(lines) + '\n')

    def document(path, value):
        target = pack / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2))

    catalog = load_catalog(city_catalog)
    cities = materialized(catalog)
    document('pack.mcmeta', {'pack': {'min_format': [88, 0], 'max_format': [88, 0],
                                      'description': 'Earthcraft travel: cities, coordinates and exploration'}})

    trigger_objectives = ('ec_scale', 'ec_speed', 'ec_mode', 'ec_city', 'ec_city_menu',
                          'ec_coord_menu', 'ec_coord_x', 'ec_coord_y', 'ec_coord_z', 'ec_coord_ready')
    function('load', [*(f'scoreboard objectives add {key} trigger' for key in trigger_objectives),
                      'scoreboard objectives add ec_tp_use minecraft.used:minecraft.carrot_on_a_stick'])
    tick = []
    for key, handler in [('ec_scale', 'scale'), ('ec_speed', 'speed'), ('ec_mode', 'mode'),
                         ('ec_city', 'city'), ('ec_city_menu', 'city_menu'),
                         ('ec_coord_menu', 'coordinate_menu'), ('ec_coord_ready', 'coordinate')]:
        tick += [f'execute as @a[scores={{{key}=1..}}] at @s run function earthcraft:travel/{handler}',
                 f'scoreboard players enable @a {key}']
    tick += [f'scoreboard players enable @a {key}' for key in ('ec_coord_x', 'ec_coord_y', 'ec_coord_z')]
    tick += [
        'execute as @a[scores={ec_tp_use=1..}] at @s if items entity @s weapon.mainhand minecraft:carrot_on_a_stick[minecraft:custom_data~{earthcraft_teleporter:1b}] run function earthcraft:travel/open',
        'scoreboard players reset @a[scores={ec_tp_use=1..}] ec_tp_use']
    function('tick', tick)
    function('scale', [
        'execute if score @s ec_scale matches 11.. run scoreboard players set @s ec_scale 10',
        'execute store result storage earthcraft:travel scale int 1 run scoreboard players get @s ec_scale',
        'function earthcraft:travel/apply_scale with storage earthcraft:travel',
        'scoreboard players reset @s ec_scale'])
    function('apply_scale', ['$attribute @s minecraft:scale base set $(scale)',
                             'tellraw @s {"text":"Player size updated. Press G for travel controls."}'])
    function('speed', [
        'execute if score @s ec_speed matches 21.. run scoreboard players set @s ec_speed 20',
        'execute store result storage earthcraft:travel speed double 0.1 run scoreboard players get @s ec_speed',
        'function earthcraft:travel/apply_speed with storage earthcraft:travel',
        'scoreboard players reset @s ec_speed'])
    function('apply_speed', ['$attribute @s minecraft:movement_speed base set $(speed)',
                             'tellraw @s {"text":"Walking speed updated. Flight speed is separate."}'])
    report = json.loads((world / 'earthcraft.json').read_text())
    x, y, z = report['spawn']
    yaw, pitch = report.get('spawn_rotation', [0, 0])
    function('home', ['attribute @s minecraft:scale base set 1',
                      'attribute @s minecraft:movement_speed base set 0.1',
                      f'tp @s {x} {y} {z} {yaw} {pitch}', 'gamemode creative @s'])
    function('mode', ['execute if score @s ec_mode matches 1 run gamemode spectator @s',
                      'execute if score @s ec_mode matches 2 run gamemode creative @s',
                      'execute if score @s ec_mode matches 3 run function earthcraft:travel/home',
                      'scoreboard players reset @s ec_mode'])

    function('open', ['dialog show @s earthcraft:travel'])
    function('cities', ['dialog show @s earthcraft:cities'])
    function('coordinates', ['dialog show @s earthcraft:coordinates'])
    function('city_menu', ['function earthcraft:travel/cities',
                           'scoreboard players reset @s ec_city_menu'])
    function('coordinate_menu', ['function earthcraft:travel/coordinates',
                                 'scoreboard players reset @s ec_coord_menu'])
    function('give_teleporter', [
        "give @s minecraft:carrot_on_a_stick[minecraft:custom_name='{\"text\":\"Earthcraft Teleporter\",\"color\":\"aqua\",\"italic\":false}',minecraft:lore=['{\"text\":\"Right-click to open the teleport list\",\"color\":\"gray\",\"italic\":false}'],minecraft:custom_data={earthcraft_teleporter:1b}] 1",
        'tellraw @s {"text":"Earthcraft Teleporter added to your inventory.","color":"aqua"}'])
    function('coordinate', [
        'execute if score @s ec_coord_x matches ..-29999985 run scoreboard players set @s ec_coord_x -29999984',
        'execute if score @s ec_coord_x matches 29999985.. run scoreboard players set @s ec_coord_x 29999984',
        'execute if score @s ec_coord_y matches ..-64 run scoreboard players set @s ec_coord_y -64',
        'execute if score @s ec_coord_y matches 320.. run scoreboard players set @s ec_coord_y 319',
        'execute if score @s ec_coord_z matches ..-29999985 run scoreboard players set @s ec_coord_z -29999984',
        'execute if score @s ec_coord_z matches 29999985.. run scoreboard players set @s ec_coord_z 29999984',
        'execute store result storage earthcraft:travel x int 1 run scoreboard players get @s ec_coord_x',
        'execute store result storage earthcraft:travel y int 1 run scoreboard players get @s ec_coord_y',
        'execute store result storage earthcraft:travel z int 1 run scoreboard players get @s ec_coord_z',
        'function earthcraft:travel/teleport_coordinates with storage earthcraft:travel',
        'scoreboard players reset @s ec_coord_x',
        'scoreboard players reset @s ec_coord_y',
        'scoreboard players reset @s ec_coord_z',
        'scoreboard players reset @s ec_coord_ready'])
    function('teleport_coordinates', ['$tp @s $(x) $(y) $(z)',
                                      'tellraw @s {"text":"Teleported to coordinates."}'])

    def button(label, command):
        return {'label': label, 'action': {'type': 'minecraft:run_command', 'command': command}}

    actions = [
        button('City list', 'trigger ec_city_menu set 1'),
        button('Enter coordinates', 'trigger ec_coord_menu set 1'),
        {'label': 'Apply size', 'action': {'type': 'minecraft:dynamic/run_command',
                                           'template': 'trigger ec_scale set $(scale)'}},
        {'label': 'Apply walk speed', 'action': {'type': 'minecraft:dynamic/run_command',
                                                  'template': 'trigger ec_speed set $(speed)'}},
        button('Fast flight', 'trigger ec_mode set 1'),
        button('Creative mode', 'trigger ec_mode set 2'),
        button('Human at spawn', 'trigger ec_mode set 3')]
    dialog = {'type': 'minecraft:multi_action', 'title': 'Earthcraft travel',
              'external_title': 'Travel', 'columns': 2, 'pause': True,
              'body': [{'type': 'minecraft:plain_message', 'width': 280,
                        'contents': 'Cities use the shared Earthcraft globe frame. Coordinates are Minecraft X/Y/Z; the list keeps real-world city locations in one metre-scale world.'}],
              'inputs': [
                  {'type': 'minecraft:number_range', 'key': 'scale', 'label': 'Player size (times human)',
                   'start': 1, 'end': 10, 'initial': 1, 'step': 1, 'width': 300},
                  {'type': 'minecraft:number_range', 'key': 'speed', 'label': 'Walking speed multiplier',
                   'start': 1, 'end': 20, 'initial': 1, 'step': 1, 'width': 300}],
              'actions': actions, 'exit_action': {'label': 'Back'}}
    document('data/earthcraft/dialog/travel.json', dialog)

    city_actions = []
    for index, city in enumerate(cities, 1):
        target = city['target']
        message = json.dumps({'text': 'Arrived at ' + city['name']}, separators=(',', ':'))
        function(f'city_{index}', [f'tp @s {target[0]} {target[1]} {target[2]}',
                                   f'tellraw @s {message}'])
        city_actions.append(button(city['name'], f'trigger ec_city set {index}'))
    function('city', [*(f'execute if score @s ec_city matches {index} run function earthcraft:travel/city_{index}'
                        for index in range(1, len(cities) + 1)),
                      'scoreboard players reset @s ec_city'])
    city_body = ('Select a materialized city. Destinations are held in the shared Earthcraft frame; '
                 'unbuilt catalog entries stay out of the action list.')
    document('data/earthcraft/dialog/cities.json', {
        'type': 'minecraft:multi_action', 'title': 'Earthcraft cities',
        'external_title': 'Cities', 'columns': 2, 'pause': True,
        'body': [{'type': 'minecraft:plain_message', 'width': 280, 'contents': city_body}],
        'actions': city_actions, 'exit_action': {'label': 'Back'}})
    document('data/earthcraft/dialog/coordinates.json', {
        'type': 'minecraft:multi_action', 'title': 'Earthcraft coordinates',
        'external_title': 'Coordinates', 'columns': 1, 'pause': True,
        'body': [{'type': 'minecraft:plain_message', 'width': 280,
                  'contents': 'Enter integer Minecraft coordinates. Values are clamped to the playable world envelope.'}],
        'inputs': [
            {'type': 'minecraft:number_range', 'key': 'x', 'label': 'X (east +)', 'start': -29999984, 'end': 29999984, 'initial': 0, 'step': 1, 'width': 300},
            {'type': 'minecraft:number_range', 'key': 'y', 'label': 'Y (elevation)', 'start': -64, 'end': 319, 'initial': 80, 'step': 1, 'width': 300},
            {'type': 'minecraft:number_range', 'key': 'z', 'label': 'Z (south +)', 'start': -29999984, 'end': 29999984, 'initial': 0, 'step': 1, 'width': 300}],
        'actions': [
            {'label': 'Set X', 'action': {'type': 'minecraft:dynamic/run_command',
                                          'template': 'trigger ec_coord_x set $(x)'}},
            {'label': 'Set Y', 'action': {'type': 'minecraft:dynamic/run_command',
                                          'template': 'trigger ec_coord_y set $(y)'}},
            {'label': 'Set Z', 'action': {'type': 'minecraft:dynamic/run_command',
                                          'template': 'trigger ec_coord_z set $(z)'}},
            button('Teleport', 'trigger ec_coord_ready set 1')],
        'exit_action': {'label': 'Back'}})
    document('data/minecraft/tags/function/load.json', {'values': ['earthcraft:travel/load']})
    document('data/minecraft/tags/function/tick.json', {'values': ['earthcraft:travel/tick']})
    for tag in ('quick_actions', 'pause_screen_additions'):
        document(f'data/minecraft/tags/dialog/{tag}.json', {'values': ['earthcraft:travel']})

    level = n.load(world / 'level.dat')
    enabled = level['Data']['DataPacks']['Enabled']
    enabled.append(n.String('file/earthcraft_travel'))
    level.save(world / 'level.dat')
    result = {'open': '/function earthcraft:travel/give_teleporter, then right-click; /function earthcraft:travel/open',
              'player_scale_range': [1, 10],
              'walk_speed_range': [1, 20], 'fast_flight': 'Spectator mode; mouse wheel adjusts speed',
              'city_count': len(cities), 'coordinate_teleport': True,
              'teleporter_item': 'Earthcraft Teleporter (carrot on a stick)',
              'teleporter_commands': ['/function earthcraft:travel/give_teleporter',
                                      '/function earthcraft:travel/open',
                                      '/function earthcraft:travel/cities',
                                      '/function earthcraft:travel/coordinates'],
              'coordinate_system': catalog['coordinate_order'],
              'changes_geographic_blocks': False, 'llm_used': False,
              'ui_playtest_verified': False,
              'limitations': ['Sliders start at defaults when reopened; they do not display current values.',
                              'Grow outdoors; a large body cannot fit through human-sized openings.',
                              'Coordinate teleport does not generate missing terrain; unscanned surroundings remain void.']}
    (world / 'travel-controls.json').write_text(json.dumps(result, indent=2))
    return result

"""Small native Java 1.21.10 travel menu. No client mod or typed commands."""
import json
from pathlib import Path
import nbtlib as n


def install_controls(world):
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
    document('pack.mcmeta', {'pack': {'min_format': [88, 0], 'max_format': [88, 0],
                                     'description': 'Earthcraft travel: player scale and exploration'}})
    objectives = ('ec_scale', 'ec_speed', 'ec_mode')
    function('load', [f'scoreboard objectives add {key} trigger' for key in objectives])
    tick = []
    for key, handler in [('ec_scale', 'scale'), ('ec_speed', 'speed'), ('ec_mode', 'mode')]:
        tick += [f'execute as @a[scores={{{key}=1..}}] at @s run function earthcraft:travel/{handler}',
                 f'scoreboard players enable @a {key}']
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
    for tag, value in [('load', 'load'), ('tick', 'tick')]:
        document(f'data/minecraft/tags/function/{tag}.json', {'values': [f'earthcraft:travel/{value}']})
    def button(label, command):
        return {'label': label, 'action': {'type': 'minecraft:run_command', 'command': command}}
    dialog = {'type': 'minecraft:multi_action', 'title': 'Earthcraft travel',
        'external_title': 'Travel', 'columns': 2, 'pause': True,
        'body': [{'type': 'minecraft:plain_message', 'width': 280,
            'contents': 'Grow outdoors; map stays 1:1. Fast flight: scroll to adjust speed. Outside this tile is unscanned void; Human at spawn returns you. Sliders select new values, not current settings.'}],
        'inputs': [
            {'type': 'minecraft:number_range', 'key': 'scale', 'label': 'Player size (times human)',
             'start': 1, 'end': 10, 'initial': 1, 'step': 1, 'width': 300},
            {'type': 'minecraft:number_range', 'key': 'speed', 'label': 'Walking speed multiplier',
             'start': 1, 'end': 20, 'initial': 1, 'step': 1, 'width': 300}],
        'actions': [
            {'label': 'Apply size', 'action': {'type': 'minecraft:dynamic/run_command',
                                             'template': 'trigger ec_scale set $(scale)'}},
            {'label': 'Apply walk speed', 'action': {'type': 'minecraft:dynamic/run_command',
                                                   'template': 'trigger ec_speed set $(speed)'}},
            button('Fast flight', 'trigger ec_mode set 1'),
            button('Creative mode', 'trigger ec_mode set 2'),
            button('Human at spawn', 'trigger ec_mode set 3')],
        'exit_action': {'label': 'Back'}}
    document('data/earthcraft/dialog/travel.json', dialog)
    for tag in ('quick_actions', 'pause_screen_additions'):
        document(f'data/minecraft/tags/dialog/{tag}.json', {'values': ['earthcraft:travel']})
    level = n.load(world / 'level.dat')
    enabled = level['Data']['DataPacks']['Enabled']
    enabled.append(n.String('file/earthcraft_travel'))
    level.save(world / 'level.dat')
    result = {'open': 'G or Pause → Travel', 'player_scale_range': [1, 10],
              'walk_speed_range': [1, 20], 'fast_flight': 'Spectator mode; mouse wheel adjusts speed',
              'changes_geographic_blocks': False, 'llm_used': False,
              'ui_playtest_verified': False,
              'limitations': ['Sliders start at defaults when reopened; they do not display current values.',
                              'Grow outdoors; a large body cannot fit through human-sized openings.']}
    (world / 'travel-controls.json').write_text(json.dumps(result, indent=2))
    return result

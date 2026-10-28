"""Telegram peer color palettes (mirrors official help.getPeerColors output)."""
from mtproto import tl

_HELP_PEER_COLOR_OPTION = 0xadec6ebe
_HELP_PEER_COLOR_SET = 0x26219a58
_HELP_PEER_COLOR_PROFILE_SET = 0x767D61EB

HELP_MESSAGE_COLORS_HASH = -727783590
HELP_PROFILE_COLORS_HASH = 1743258713

MAX_MESSAGE_COLOR_ID = 20
MAX_PROFILE_COLOR_ID = 15

_MESSAGE_COLOR_OPTIONS = [
    {'id': 5},
    {'id': 3},
    {'id': 1},
    {'id': 0},
    {'id': 2},
    {'id': 4},
    {'id': 6},
    {'id': 12, 'light': [3379668, 8246256], 'dark': [5423103, 742548]},
    {'id': 10, 'light': [2599184, 11000919], 'dark': [11004782, 1474093]},
    {'id': 8, 'light': [14712875, 16434484], 'dark': [15511630, 12801812]},
    {'id': 7, 'light': [14766162, 16363107], 'dark': [16749440, 10039095]},
    {'id': 9, 'light': [10510323, 16027647], 'dark': [13015039, 6173128]},
    {'id': 11, 'light': [2600142, 8579286], 'dark': [4249808, 285823]},
    {'id': 13, 'light': [14500721, 16760479], 'dark': [16746150, 9320046]},
    {'id': 14, 'light': [2391021, 15747158, 16777215],
     'dark': [4170494, 15024719, 16777215]},
    {'id': 15, 'light': [14055202, 2007057, 16777215],
     'dark': [16748638, 3319079, 16777215]},
    {'id': 16, 'light': [1547842, 15223359, 16777215],
     'dark': [6738788, 13976655, 16777215]},
    {'id': 17, 'light': [2659503, 7324758, 16777215],
     'dark': [2276578, 4039232, 16777215]},
    {'id': 18, 'light': [826035, 16756117, 16770741],
     'dark': [2276578, 16750456, 16767595]},
    {'id': 19, 'light': [7821270, 16225808, 16768654],
     'dark': [9933311, 15889181, 16767833]},
    {'id': 20, 'light': [1410511, 15903517, 16777215],
     'dark': [4040427, 15639837, 16777215]},
]

_PROFILE_COLOR_OPTIONS = [
    {'id': 5, 'palette': [4888278], 'bg': [5935035], 'story': [7264511, 7405535],
     'dark_palette': [3375297], 'dark_bg': [4682132], 'dark_story': [9029631, 7536638]},
    {'id': 3, 'palette': [5485111], 'bg': [4825941], 'story': [7991418, 13299018],
     'dark_palette': [3972130], 'dark_bg': [3371323], 'dark_story': [7919501, 12703080]},
    {'id': 1, 'palette': [14386489], 'bg': [12745790], 'story': [16756531, 16240230],
     'dark_palette': [12807972], 'dark_bg': [9723436], 'dark_story': [16756531, 16240230]},
    {'id': 0, 'palette': [13722204], 'bg': [12211792], 'story': [16752253, 16758622],
     'dark_palette': [12209223], 'dark_bg': [10241344], 'dark_story': [16744573, 16745797]},
    {'id': 2, 'palette': [10513887], 'bg': [9792200], 'story': [16030463, 16753387],
     'dark_palette': [9000906], 'dark_bg': [7426201], 'dark_story': [15366399, 16755185]},
    {'id': 4, 'palette': [4565185], 'bg': [4102061], 'story': [5036799, 4325314],
     'dark_palette': [3646897], 'dark_bg': [3702407], 'dark_story': [4708863, 3342285]},
    {'id': 6, 'palette': [13460119], 'bg': [12079992], 'story': [16746153, 16754323],
     'dark_palette': [11947138], 'dark_bg': [9717603], 'dark_story': [16675727, 16751486]},
    {'id': 7, 'palette': [9477803], 'bg': [8358805], 'story': [12834019, 15726847],
     'dark_palette': [7964822], 'dark_bg': [4412001], 'dark_story': [10071235, 14871283]},
    {'id': 13, 'palette': [3574481, 8246256], 'bg': [5475266, 5089469],
     'story': [7264511, 7405535],
     'dark_palette': [5148620, 7525869], 'dark_bg': [3694988, 4557729],
     'dark_story': [9029631, 7536638]},
    {'id': 11, 'palette': [2599184, 11000919], 'bg': [4036437, 9021008],
     'story': [7991418, 13299018],
     'dark_palette': [4036437, 10932055], 'dark_bg': [2714179, 6262596],
     'dark_story': [7919501, 12703080]},
    {'id': 9, 'palette': [14712875, 15842348], 'bg': [13595204, 13407283],
     'story': [16756531, 16240230],
     'dark_palette': [13595204, 15247677], 'dark_bg': [9393455, 10580530],
     'dark_story': [16756531, 16240230]},
    {'id': 8, 'palette': [14966882, 16363107], 'bg': [13194845, 14253143],
     'story': [16752253, 16758622],
     'dark_palette': [13194845, 16486759], 'dark_bg': [10044227, 11294782],
     'dark_story': [16744573, 16745797]},
    {'id': 10, 'palette': [10510323, 16027647], 'bg': [9855700, 12150454],
     'story': [16030463, 16753387],
     'dark_palette': [9855700, 15236580], 'dark_bg': [6506129, 9588898],
     'dark_story': [15366399, 16755185]},
    {'id': 12, 'palette': [2600142, 9234906], 'bg': [4036026, 5287320],
     'story': [5036799, 4325314],
     'dark_palette': [3774400, 7986629], 'dark_bg': [3173500, 4102270],
     'dark_story': [4708863, 3342285]},
    {'id': 14, 'palette': [13715826, 16760479], 'bg': [11554676, 13723245],
     'story': [16746153, 16754323],
     'dark_palette': [12865666, 15830166], 'dark_bg': [8929632, 10900057],
     'dark_story': [16675727, 16751486]},
    {'id': 15, 'palette': [7108740, 11384769], 'bg': [6517890, 8096407],
     'story': [12834019, 15726847],
     'dark_palette': [7108740, 11384769], 'dark_bg': [5464174, 3688020],
     'dark_story': [10071235, 14871283]},
]


def _pack_help_peer_color_set(colors: list[int]) -> bytes:
    w = tl.Writer()
    w.u32(_HELP_PEER_COLOR_SET)
    w.u32(tl.VECTOR)
    w.i32(len(colors))
    for c in colors:
        w.i32(int(c) & 0xFFFFFF)
    return w.bytes_out()


def _pack_help_peer_color_profile_set(*, palette: list[int], bg: list[int],
                                      story: list[int]) -> bytes:
    w = tl.Writer()
    w.u32(_HELP_PEER_COLOR_PROFILE_SET)
    w.u32(tl.VECTOR)
    w.i32(len(palette))
    for c in palette:
        w.i32(int(c) & 0xFFFFFF)
    w.u32(tl.VECTOR)
    w.i32(len(bg))
    for c in bg:
        w.i32(int(c) & 0xFFFFFF)
    w.u32(tl.VECTOR)
    w.i32(len(story))
    for c in story:
        w.i32(int(c) & 0xFFFFFF)
    return w.bytes_out()


def _pack_message_color_option(opt: dict) -> bytes:
    w = tl.Writer()
    w.u32(_HELP_PEER_COLOR_OPTION)
    color_id = int(opt['id'])
    if 'light' in opt:
        w.i32((1 << 1) | (1 << 2))
        w.i32(color_id)
        w.raw(_pack_help_peer_color_set(opt['light']))
        w.raw(_pack_help_peer_color_set(opt['dark']))
    else:
        w.i32(0)
        w.i32(color_id)
    return w.bytes_out()


def _pack_profile_color_option(opt: dict) -> bytes:
    w = tl.Writer()
    w.u32(_HELP_PEER_COLOR_OPTION)
    w.i32((1 << 1) | (1 << 2))
    w.i32(int(opt['id']))
    w.raw(_pack_help_peer_color_profile_set(
        palette=opt['palette'], bg=opt['bg'], story=opt['story']))
    w.raw(_pack_help_peer_color_profile_set(
        palette=opt['dark_palette'], bg=opt['dark_bg'], story=opt['dark_story']))
    return w.bytes_out()


def pack_help_peer_colors(req_hash: int, *, profile: bool = False) -> bytes:
    cur_hash = HELP_PROFILE_COLORS_HASH if profile else HELP_MESSAGE_COLORS_HASH
    options = _PROFILE_COLOR_OPTIONS if profile else _MESSAGE_COLOR_OPTIONS
    if req_hash and int(req_hash) == int(cur_hash):
        w = tl.Writer()
        w.u32(tl.HELP_PEER_COLORS_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(0xf8ed08)  # help.peerColors
    w.i32(int(cur_hash))
    w.u32(tl.VECTOR)
    w.i32(len(options))
    packer = _pack_profile_color_option if profile else _pack_message_color_option
    for opt in options:
        w.raw(packer(opt))
    return w.bytes_out()


def is_valid_color_id(color_id: int, *, for_profile: bool) -> bool:
    if color_id < 0:
        return True
    return color_id <= (MAX_PROFILE_COLOR_ID if for_profile else MAX_MESSAGE_COLOR_ID)

from collections import defaultdict
from typing import Optional 

def hotkey(name: str = '', key: str = '', mode: Optional[str] = None, ctrl: bool = False, alt: bool = False):
    def decorator(func):
        if key and name or (not key and not name):
            raise Error('either key or name should be specified')
        hotkeys = getattr(func, '__hotkeys__', None)
        if not hotkeys:
            func.__hotkeys__ = []
        func.__hotkeys__.append({
            'key': key if key else name,
            'mode': mode,
            'ctrl': ctrl,
            'alt': alt
        })

        return func


    return decorator

def unhandled_key_handler():
    def decorator(func):
        func.__unhandled_key_handler__ = True
        return func

    return decorator


class HotkeysMeta(type):
    def __new__(cls, name, bases, attrs):
        keymap = defaultdict(dict)
        # TODO: a lot of keymaps are messy, maybe some general solution?
        ctrl_keymap = defaultdict(dict)
        alt_keymap = defaultdict(dict)
        ctrl_alt_keymap = defaultdict(dict)
        handle_unhandled = None

        for base in bases:
            base_keymap = getattr(base, 'keymap', None)
            if base_keymap:
                for mode, mapping in base_keymap.items():
                    keymap[mode] = mapping.copy()

        for attr_name, value in attrs.items():
            unhandled_key_handler = getattr(value, "__unhandled_key_handler__", None)
            if unhandled_key_handler:
                handle_unhandled = value

            hotkeys = getattr(value, "__hotkeys__", None)
            if hotkeys is None:
                continue
            for hotkey in hotkeys:
                if hotkey['ctrl']:
                    if hotkey['alt']:
                        hotkey_keymap = ctrl_alt_keymap
                    else:
                        hotkey_keymap = ctrl_keymap
                elif hotkey['alt']:
                    hotkey_keymap = alt_keymap
                else:
                    hotkey_keymap = keymap

                hotkey_keymap[hotkey['mode']][hotkey['key']] = value



        attrs['keymap'] = keymap
        attrs['ctrl_keymap'] = ctrl_keymap
        attrs['alt_keymap'] = alt_keymap
        attrs['ctrl_alt_keymap'] = ctrl_alt_keymap

        def handle_key(self, name: str, key: str, ctrl: bool, alt: bool):
            if ctrl:
                if alt:
                    keymap = self.ctrl_alt_keymap
                else:
                    keymap = self.ctrl_keymap
            elif alt:
                keymap = self.alt_keymap
            else:
                keymap = self.keymap

            handler = keymap[self.mode].get(key)
            if handler:
                handler(self)
            elif handler := self.keymap[self.mode].get(name):
                handler(self)
            elif handler := keymap[None].get(key):
                handler(self)
            elif handler := self.keymap[None].get(name):
                handler(self)
            elif handle_unhandled:
                handle_unhandled(self, name, key, ctrl, alt)

        cls = super().__new__(cls, name, bases, attrs)
        cls.handle_key = handle_key

        return cls


# kanso

Personal, local-first todo app with a terminal UI, also served in the browser.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Linux (x86_64 or aarch64), macOS (Apple silicon) or Windows (x86_64), the platforms
  that [iroh](https://iroh.computer), used for peer-to-peer sync, ships Python wheels for

## Run

```sh
uv sync
uv run kanso          # terminal UI
uv run kanso-web      # same UI in the browser on http://localhost:8080
```

Data is stored in a local SQLite database in the user data directory
(`~/.local/share/kanso/kanso.db` on Linux). Set `KANSO_DATA_DIR` to keep it
elsewhere: `KANSO_DATA_DIR=~/kanso uv run kanso`.

## Sync between devices

Devices sync peer to peer while kanso runs on them. To join two devices, and
with them every device each one already syncs with:

1. On one device, press `c`. It shows this device's ID and a pairing code,
   valid for 10 minutes and usable once (`ctrl+y` copies both).
2. On the other device, press `c` and enter that ID and code.

From then on, changes reach every device within seconds when they're online,
and as soon as they come back otherwise. When the same field of a todo was
edited on two devices meanwhile, the later edit wins; edits to different fields
both survive. The header shows how many devices are online.

- Connections use [iroh](https://iroh.computer): devices find each other by ID,
  through NATs, with help from n0's public relays and DNS. Traffic is encrypted
  end to end; the relays see only device IDs and IP addresses.
- Paired devices are fully trusted: any of them can change or delete anything.
- Only one kanso process per data directory syncs (a `sync.lock` there says
  which); other windows, such as browser tabs of `kanso-web`, share its data.
- Don't copy the data directory to another machine, or keep it in a synced
  folder: the copy would be the same device as far as sync is concerned. Pair
  the machines instead.
- A device running an older kanso can't take changes from a newer one until it's
  updated; the newer one still takes its changes.

## Install on NixOS

Try it without installing:

```sh
nix run github:zer0pr1me/kanso
```

To install it, add the flake as an input of your system flake and pass it to
home-manager (for example via `home-manager.extraSpecialArgs`):

```nix
inputs.kanso = {
  url = "github:zer0pr1me/kanso";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

Then, in a home-manager user configuration:

```nix
{ kanso, config, ... }:
{
  imports = [ kanso.homeManagerModules.default ];

  programs.kanso = {
    enable = true;
    # Optional; defaults to ~/.local/share/kanso.
    dataDir = "${config.home.homeDirectory}/kanso";
  };
}
```

This installs both `kanso` and `kanso-web`. `dataDir` is baked into them as the
default `KANSO_DATA_DIR`; setting the variable yourself still takes precedence.
The package is built by `nix build` (for `x86_64-linux` and `aarch64-linux`),
which also runs the test suite.

## Develop

```sh
uv run pytest         # in parallel; add -n0 to run serially, e.g. to use a debugger
KANSO_NETWORK_TESTS=1 uv run pytest   # also connect real iroh nodes (needs internet)
scripts/install-hooks.sh   # once per clone: run the tests before every commit
```

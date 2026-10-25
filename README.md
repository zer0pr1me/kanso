# kanso

Personal, local-first todo app with a terminal UI, also served in the browser.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Run

```sh
uv sync
uv run kanso          # terminal UI
uv run kanso-web      # same UI in the browser on http://localhost:8080
```

Data is stored in a local SQLite database in the user data directory
(`~/.local/share/kanso/kanso.db` on Linux). Set `KANSO_DATA_DIR` to keep it
elsewhere: `KANSO_DATA_DIR=~/Sync/kanso uv run kanso`.

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
    dataDir = "${config.home.homeDirectory}/Sync/kanso";
  };
}
```

This installs both `kanso` and `kanso-web`. `dataDir` is baked into them as the
default `KANSO_DATA_DIR`; setting the variable yourself still takes precedence.
The package is built by `nix build` (for `x86_64-linux` and `aarch64-linux`),
which also runs the test suite.

## Develop

```sh
uv run pytest
scripts/install-hooks.sh   # once per clone: run the tests before every commit
```

self:
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.kanso;
in
{
  options.programs.kanso = {
    enable = lib.mkEnableOption "kanso, a local-first todo app";
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      description = "The kanso package to install.";
    };
    dataDir = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = lib.literalExpression ''"''${config.home.homeDirectory}/Sync/kanso"'';
      description = ''
        Directory holding the kanso database. `null` keeps the default,
        `~/.local/share/kanso`. A `KANSO_DATA_DIR` set in the environment still wins.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [
      (
        if cfg.dataDir == null then
          cfg.package
        else
          pkgs.symlinkJoin {
            name = "kanso-${cfg.package.version}";
            paths = [ cfg.package ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              for bin in $out/bin/*; do
                wrapProgram "$bin" --set-default KANSO_DATA_DIR ${lib.escapeShellArg cfg.dataDir}
              done
            '';
            inherit (cfg.package) meta;
          }
      )
    ];
  };
}

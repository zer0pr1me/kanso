self:
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.kanso;
  package =
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
      };
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
      example = lib.literalExpression ''"''${config.home.homeDirectory}/kanso"'';
      description = ''
        Directory holding the kanso database. `null` keeps the default,
        `~/.local/share/kanso`. A `KANSO_DATA_DIR` set in the environment still wins.
      '';
    };
    daemon.enable = lib.mkEnableOption ''
      the kanso daemon as a systemd user service, from login on. Home-manager restarts it on
      switch when kanso changes, and the new kanso takes over from the daemon running. Another
      kanso started later (a dev build, say) takes over from it in turn: the service stops
      cleanly then, and starts again with the next login or switch'';
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ package ];

    systemd.user.services.kanso = lib.mkIf cfg.daemon.enable {
      Unit.Description = "kanso daemon: stores kanso's data and syncs it with your other devices";
      Service = {
        ExecStart = "${lib.getExe package} start --foreground";
        Restart = "on-failure";
        RestartSec = 10;
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}

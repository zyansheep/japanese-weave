# services.japanese-weave, a home-manager module (README "Install with Nix").
#
#   collector.enable, collector.listen   the collector as the systemd user service japanese-weave-collector, running
#                                        the package from the store (as `fw.py collector install` does with a copy);
#                                        before each start it backs up the state dir once per new version
#   client.enable                        the browser's native host manifests, pointing at the package's fw-host, and
#                                        ~/.local/share/japanese-weave/current -> the plugin tree, the stable path for
#                                        a Claude Code directory marketplace (settings.json is left to you)
{self}: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.japanese-weave;
  inherit (lib) mkEnableOption mkIf mkMerge mkOption types;
  host = "com.japaneseweave.host"; # fw.py HOST_NAME
  manifests = "${cfg.package}/lib/japanese-weave/native-messaging";
  darwin = pkgs.stdenv.hostPlatform.isDarwin;
  appSupport = "Library/Application Support";
  # Where each browser looks for user-level native messaging hosts (fw.py host_dirs), relative to home.
  hostDirs = {
    firefox =
      if darwin
      then "${appSupport}/Mozilla/NativeMessagingHosts"
      else ".mozilla/native-messaging-hosts";
    chrome =
      if darwin
      then "${appSupport}/Google/Chrome/NativeMessagingHosts"
      else "${lib.removePrefix "${config.home.homeDirectory}/" config.xdg.configHome}/google-chrome/NativeMessagingHosts";
    chromium =
      if darwin
      then "${appSupport}/Chromium/NativeMessagingHosts"
      else "${lib.removePrefix "${config.home.homeDirectory}/" config.xdg.configHome}/chromium/NativeMessagingHosts";
  };
  manifestFor = browser:
    if browser == "firefox"
    then "${manifests}/firefox.json"
    else "${manifests}/chrome.json";
  backup = "${cfg.package}/bin/japanese-weave-backup --keep ${toString cfg.backups}";
in {
  options.services.japanese-weave = {
    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "japanese-weave.packages.\${system}.default";
      description = "The Japanese Weave package.";
    };

    backups = mkOption {
      type = types.ints.positive;
      default = 5;
      description = ''
        How many snapshots of ~/.japanese-weave to keep in its backups/ folder. One is taken before a new version
        first runs: by the collector's service before it starts, else (client only) on each activation.
      '';
    };

    collector = {
      enable = mkEnableOption ''
        the Japanese Weave collector, the one home of the word state and the gloss log for every machine, as the
        systemd user service japanese-weave-collector (enable lingering so it runs while logged out)'';
      listen = mkOption {
        type = types.str;
        example = "100.100.100.100:7853";
        description = "HOST:PORT to listen on: the private network's address of this machine (the Tailscale IP), never every interface.";
      };
    };

    client = {
      enable = mkEnableOption ''
        the Japanese Weave client side: the native host manifests for the browser extension, and
        ~/.local/share/japanese-weave/current, the stable path for the Claude Code marketplace'';
      browsers = mkOption {
        type = types.listOf (types.enum ["firefox" "chrome" "chromium"]);
        default = ["firefox" "chrome" "chromium"];
        description = "The browsers to register the native host for (Firefox's covers Floorp and other forks that read ~/.mozilla).";
      };
    };
  };

  config = mkMerge [
    (mkIf (cfg.collector.enable || cfg.client.enable) {
      home.packages = [cfg.package]; # fw, fw-host, japanese-weave-backup
    })

    (mkIf cfg.collector.enable {
      systemd.user.services.japanese-weave-collector = {
        Unit = {
          Description = "Japanese Weave collector (gloss interactions from every machine)";
          After = ["network-online.target"];
          Wants = ["network-online.target"];
        };
        Service = {
          # Before a new version first runs: state.json and the rest, and interactions.sqlite through SQLite's backup
          # API, into ~/.japanese-weave/backups/<version>-<time>. Fails the start if the backup fails.
          ExecStartPre = backup;
          ExecStart = "${cfg.package}/bin/fw collector run --listen ${cfg.collector.listen}";
          Restart = "on-failure";
          RestartSec = 10;
          # collector.REFUSED: the state dir has a "collector" file (it points at another collector), so starting
          # again would only fail again.
          RestartPreventExitStatus = 78;
        };
        Install.WantedBy = ["default.target"];
      };
    })

    (mkIf cfg.client.enable {
      xdg.dataFile."japanese-weave/current".source = "${cfg.package}/share/japanese-weave";
      home.file = lib.listToAttrs (map (browser: {
          name = "${hostDirs.${browser}}/${host}.json";
          value.source = manifestFor browser;
        })
        cfg.client.browsers);
    })

    (mkIf (cfg.client.enable && !cfg.collector.enable) {
      # The collector's service backs up on its own machine; a client backs up its cache (and any changes queued for
      # the collector) once per new version, as it is switched to.
      home.activation.japaneseWeaveBackup = lib.hm.dag.entryAfter ["writeBoundary"] ''
        run ${backup} --quiet
      '';
    })
  ];
}

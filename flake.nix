# Japanese Weave as a Nix package and a home-manager module, for deploying it through a NixOS configuration instead
# of a checkout (README "Install with Nix").
#
#   packages.<system>.default   the plugin tree, read-only, in $out/share/japanese-weave (the directory a Claude Code
#                               directory marketplace points at), with scripts/run on the package's own Python and
#                               node; $out/bin/fw (fw.py), fw-host (the browser's native host) and
#                               japanese-weave-backup (scripts/backup.py at this version)
#   homeManagerModules.default  services.japanese-weave: the collector as a systemd user service, and the client side
#                               (native host manifests, ~/.local/share/japanese-weave/current)
#   checks.<system>.tests       the Python tests run against the installed, read-only tree
{
  description = "Japanese Weave: passive Japanese in LLM chat replies and web pages";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin"];
    forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    # The date of the commit and its short hash: what the backups and the collector's log go by.
    version = "${builtins.substring 0 8 (self.lastModifiedDate or "19700101")}-${self.shortRev or self.dirtyShortRev or "dev"}";
  in {
    packages = forAll (pkgs: rec {
      japanese-weave = pkgs.callPackage ./nix/package.nix {inherit version;};
      default = japanese-weave;
    });

    homeManagerModules.default = import ./nix/module.nix {inherit self;};

    checks = forAll (pkgs: {
      tests = pkgs.callPackage ./nix/tests.nix {
        japanese-weave = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        tests = ./tests;
        tools = ./tools;
      };
    });

    formatter = forAll (pkgs: pkgs.alejandra);
  };
}

{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    flake-utils = {
      url = "github:numtide/flake-utils";
    };
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
      };

      lib = nixpkgs.lib;
    in {
      packages = rec {
        syncbot = pkgs.python3Packages.buildPythonApplication {
          pname = "syncbot";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = with pkgs.python3Packages; [
            hatchling
          ];

          dependencies = with pkgs.python3Packages; [
            requests
          ];

          meta = {
            description = "Syncbot";
            homepage = "https://github.com/CPlusPatch/syncbot";
            license = lib.licenses.gpl3Only;
          };
        };
        default = syncbot;
      };

      apps = rec {
        syncbot = flake-utils.lib.mkApp {
          drv = self.packages.${system}.syncbot;
          exePath = "/lib/python3.12/site-packages/syncbot/__init__.py";
        };
        default = syncbot;
      };

      nixosModules = {
        syncbot = {
          config,
          lib,
          pkgs,
          ...
        }: let
          cfg = config.services.syncbot;

          inherit (lib.options) mkOption;
          inherit (lib.modules) mkIf;
        in {
          options.services.syncbot = {
            enable = mkOption {
              type = lib.types.bool;
              default = false;
              description = "Whether to enable the syncbot service";
            };

            dataDir = mkOption {
              type = lib.types.str;
              default = "/var/lib/syncbot";
              description = "Path to the data directory for the bot";
            };
          };

          config = mkIf cfg.enable {
            systemd.services.syncbot = {
              after = ["network-online.target"];
              wantedBy = ["multi-user.target"];
              requires = ["network-online.target"];

              description = "Syncbot service";

              serviceConfig = {
                ExecStart = self.apps.${system}.default.program;
                Type = "simple";
                Restart = "always";
                RestartSec = "5s";

                User = "syncbot";
                Group = "syncbot";

                StateDirectory = "syncbot";
                StateDirectoryMode = "0700";
                RuntimeDirectory = "syncbot";
                RuntimeDirectoryMode = "0700";

                # Set the working directory to the data directory
                WorkingDirectory = cfg.dataDir;

                StandardOutput = "journal";
                StandardError = "journal";
                SyslogIdentifier = "syncbot";
              };
            };

            users.users.syncbot = {
              name = "syncbot";
              group = "syncbot";
              home = cfg.dataDir;
              isSystemUser = true;
              packages = [
                self.packages.${system}.default
              ];
            };

            users.groups.syncbot = {};
          };
        };
      };
    });
}

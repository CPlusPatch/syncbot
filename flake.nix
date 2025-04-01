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
    in {
      packages = rec {
        syncbot = pkgs.python3Packages.buildPythonPackage {
          pname = "syncbot";
          version = "0.1.0";
          src = ./.;
          format = "pyproject";
          propagatedBuildInputs = with pkgs.python3.pkgs; [
            hatchling
            requests
          ];
        };
        default = syncbot;
      };

      apps = rec {
        syncbot = flake-utils.lib.mkApp {
          drv = self.packages.${system}.syncbot;
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
          configFile = configFormat.generate "config.json" cfg.config;
          configFormat = pkgs.formats.json {};

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

            config = mkOption {
              type = with lib.types;
                submodule {
                  freeformType = configFormat.type;
                  options = {
                  };
                };
              description = "Contents of the config file for the syncbot service";
              default = {};
            };
          };

          config = mkIf cfg.enable {
            systemd.services.syncbot = {
              after = ["network-online.target"];
              wantedBy = ["multi-user.target"];
              requires = ["network-online.target"];

              description = "Syncbot service";

              serviceConfig = {
                ExecStart = "${self.packages.${system}.default}/bin/syncbot";
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

                Environment = "CONFIG_FILE=${configFile}";
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

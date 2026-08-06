{
  description = "Reproducible development environment for hermes-emacs-plugin";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };
      pythonEnvFor = pkgs: pkgs.python3.withPackages (pythonPackages: with pythonPackages; [
        httpx
        mcp
        python-dotenv
      ]);
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          pythonEnv = pythonEnvFor pkgs;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.uv
            ];

            shellHook = ''
              export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
            '';
          };
        });

      checks = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          pythonEnv = pythonEnvFor pkgs;
        in
        {
          default = pkgs.runCommand "hermes-emacs-plugin-tests" {
            nativeBuildInputs = [ pythonEnv ];
          } ''
            cp -R ${self} source
            chmod -R u+w source
            cd source
            export PYTHONPATH="$PWD"
            python -m compileall hermes_emacs_plugin
            python -m unittest discover -s tests
            touch $out
          '';
        });
    };
}

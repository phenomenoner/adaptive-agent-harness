# Hermes host profile

Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so
`aar-mcp` and its declared IPython, NumPy, and pandas dependencies are on the Hermes host PATH,
then install this directory with `hermes profile install <directory> --name <profile>`. The runtime
reads `config.yaml.mcp_servers`; `mcp.json` is the equivalent reviewable server map.
Use a fresh isolated profile and invoke the bundled `aar-operations` skill.
The same surface supports bounded RLM jobs and immutable asset bundles without serving
activation authority.

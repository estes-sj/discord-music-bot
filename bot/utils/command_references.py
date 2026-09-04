def load_guild_config(store, guild_id, defaults):
    if guild_id is None or store is None:
        return defaults.copy()
    return store.get(guild_id, defaults)


def format_command_reference(config, slash_command, prefix_command=None):
    references = []
    if config["slash_commands_enabled"]:
        references.append(f"`/{slash_command}`")
    if config["prefix_commands_enabled"]:
        prefix_command = prefix_command or slash_command
        references.append(f"`{config['command_prefix']}{prefix_command}`")
    return " or ".join(references)

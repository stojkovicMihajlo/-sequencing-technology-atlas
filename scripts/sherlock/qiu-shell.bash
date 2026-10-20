# Explicit rc file for `ssh sherlock-qiu`; not a global shell startup file.
if [[ -r "$HOME/.bashrc" ]]; then
    source "$HOME/.bashrc"
fi
_atlas_qiu_saved_options=$(set +o)
if source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh \
    && source "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas/scripts/sherlock/environment.sh" \
    && source "$ATLAS_ENV_DIR/bin/activate" \
    && cd "$ATLAS_PROJECT_ROOT/sequencing-technology-atlas"; then
    PS1="[Qiu] ${PS1-\u@\h:\w\$ }"
else
    printf 'Qiu project initialization failed; check the paths before working.\n' >&2
    exit 1
fi
# The batch loader uses strict options; restore the interactive user's options.
eval "$_atlas_qiu_saved_options"
unset _atlas_qiu_saved_options

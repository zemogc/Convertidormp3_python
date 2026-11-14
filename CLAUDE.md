
## Identidad de git en commits (obligatorio)

Esta regla aplica a toda sesión de Claude Code (local o en la nube) que trabaje en este repo.

1. **Antes de cualquier commit**, configura la identidad de git con los datos de GitHub de **quien lanzó la sesión** (nunca "Claude" ni correos de anthropic). Si la sesión la lanzó Zemog:

   ```bash
   git config user.name "Zemog Irneh Gomez Tabares"
   git config user.email "148599261+zemogc@users.noreply.github.com"
   ```

   Si la lanzó otra persona (p. ej. un colaborador), usa el nombre y el correo `noreply` de GitHub de esa persona.

2. **Prohibido** añadir en mensajes de commit o en descripciones de PR: líneas `Co-Authored-By`, `Claude-Session`, "Generated with Claude Code" o enlaces a sesiones de claude.ai.

3. **Antes de hacer push**, verifica el autor y el committer:

   ```bash
   git log -1 --format='%an <%ae> | %cn <%ce>'
   ```

   Si alguno dice "Claude" o contiene "anthropic", corrige el commit (`git commit --amend --reset-author` con la identidad ya configurada) antes de empujar.

MTGA Card Vault: plan del parche de seguridad

Objetivo:
- Cerrar exposición de secretos en cliente sin mezclar todavía el refactor grande.

Alcance:
- Solo `main`
- Solo seguridad
- Mantener free tier

Cambios esperados:

1. Cliente
- Eliminar claves hardcodeadas de:
  - `repo_main_app/services/geminiService.ts`
  - `repo_main_app/services/groqService.ts`
- Eliminar cualquier patrón que termine inyectando secretos al bundle:
  - revisar `repo_main_app/vite.config.ts`

2. Server-side en Vercel
- Crear endpoint mínimo:
  - sugerido: `/api/build-deck`
- Función del endpoint:
  - recibir payload pequeño
  - validar input básico
  - usar secrets desde Vercel Environment Variables
  - llamar a Gemini/Groq
  - devolver JSON compatible con el frontend actual

3. Frontend
- Reemplazar llamadas directas por fetch a `/api/build-deck`
- Mantener shape compatible con:
  - `mode`
  - `provider`
  - `archetypeTag`
  - `manaCurve`
  - `aiAnalysis`
  - `deckList`
  - `sideboard`

4. Operación post-parche
- Desplegar
- Verificar producción
- Rotar claves comprometidas

Checklist:

- No quedan claves en cliente.
- No quedan llamadas directas a Gemini/Groq desde browser.
- No se expone secreto vía `define`, env público o bundle.
- El flujo de mazos sigue funcionando.
- Claves viejas revocadas/rotadas.

Fuera de alcance:
- Python
- deck engine nuevo
- reglas deterministas fuertes
- validación real de mazos
- rediseño global de arquitectura

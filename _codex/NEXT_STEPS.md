MTGA Card Vault: próximos pasos operativos

Orden correcto de trabajo:

1. Parche de seguridad en `main`
- Sacar claves hardcodeadas del cliente.
- Sacar llamadas directas del cliente a Gemini/Groq.
- Crear endpoint server-side mínimo en Vercel.
- Mantener el frontend funcional con el mismo contrato de respuesta.

2. Verificación inmediata tras el parche
- Confirmar que el frontend sigue generando mazos.
- Confirmar que el navegador ya no ve claves.
- Confirmar que ya no hay requests directos del cliente a proveedores IA.
- Rotar todas las claves previamente expuestas.

3. Crear rama nueva desde `main`
- Nombre sugerido: `secure-ai-refactor`
- Alternativa: `deck-engine-migration`

4. Trabajo de la rama nueva
- Refactor de servicios IA.
- Feature flags si hacen falta.
- Replanteo de `build-deck`.
- Reparto de lógica determinista vs LLM.
- Preparación para una futura capa más fuerte de validación de mazos.

No hacer todavía:
- No meter Python antes de cerrar seguridad.
- No rehacer meta pipeline en esta fase.
- No tocar datasets si no es necesario para seguridad.
- No mezclar refactor cosmético con parche crítico.

Recordatorio arquitectónico:
- Git: código y datos públicos/versionados.
- Vercel: frontend + server-side mínimo seguro.
- GAS: desarrollo/prototipado, no runtime productivo.

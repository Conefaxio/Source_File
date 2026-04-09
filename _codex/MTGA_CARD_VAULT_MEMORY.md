Proyecto: MTGA Card Vault
Fecha de contexto: 2026-04-09

Repos involucrados:
- Datos/pipeline: `C:\Users\ccarr\Documents\Source_File`
- App frontend: `C:\Users\ccarr\Documents\Source_File\repo_main_app`
- Repo GitHub app: `Conefaxio/Main`
- Repo GitHub datos: `Conefaxio/Source_File`

Estado actual importante:
- Vercel ya fue apagado y la página no está visible públicamente.
- La prioridad absoluta identificada es seguridad.
- No se debe mezclar todavía seguridad con el refactor grande del deck builder.
- GAS se usa solo como entorno de desarrollo/prototipado, no como backend productivo deseado.
- GitHub es la fuente de verdad del código.
- Vercel publica frontend desde `main`.

Arquitectura real aclarada por el usuario:
- `Git -> Vercel` para publicar frontend.
- GAS no debe quedar como repositorio/sitio de producción.
- El usuario quiere mantenerse en free tier.

Conclusión arquitectónica acordada:
- Producción ideal a corto plazo: `Frontend en Vercel -> Vercel serverless/API route -> proveedor IA`
- No usar `Frontend -> proveedor IA` nunca más.
- No introducir Python todavía en la fase de seguridad.
- No introducir backend persistente todavía.
- GAS queda para desarrollo, pruebas y prototipado.

Auditoría resumida del repo app (`repo_main_app`):
- Stack: React 19 + TypeScript + Vite.
- Archivo principal: `App.tsx`
- Servicios IA actuales:
  - `services/geminiService.ts`
  - `services/groqService.ts`
- Hallazgo crítico: ambas contienen claves hardcodeadas en cliente.
- Hallazgo crítico: `vite.config.ts` tiene patrón de inyección de env al cliente; no debe usarse para secretos.
- Hallazgo crítico: `validateArenaDeck` en `services/scryfallService.ts` es un stub.
- Hallazgo importante: `forceRefreshRules`, `getRulesPool`, `triggerLocalPythonSync`, `checkBridgeStatus` están vacíos o stub.
- El deck building actual depende demasiado del LLM.
- El mejor caso actual es cuando existe un meta deck local; el peor caso es construcción casi total por intuición del modelo.

Relación entre repos:
- `Source_File` entrega:
  - `AllPrintings_MTGA_EN_ULTRA.json`
  - `Meta/*`
  - `gemini.brain.json`
  - `grooq.brain.json`
- `Main` consume esos datos y ofrece UI + búsqueda + favoritos + análisis IA.

Decisión táctica acordada:
1. Primero parche de seguridad en `main`.
2. Después crear rama nueva desde `main` para el refactor grande.
3. GAS Remix puede usarse solo como sandbox rápido, no como estrategia principal de control de cambios.

Qué debe entrar en el parche de seguridad de `main`:
- Quitar keys hardcodeadas del cliente.
- Quitar llamadas directas del cliente a Gemini/Groq.
- Crear endpoint server-side mínimo en Vercel, ejemplo: `/api/build-deck`.
- Guardar secrets solo en Vercel Environment Variables.
- Hacer que el frontend llame a la ruta interna de Vercel.
- Mantener mismo shape funcional de respuesta para no romper UI.

Qué NO debe entrar todavía:
- Refactor grande del deck builder.
- Python.
- Rediseño profundo de meta/rules pipeline.
- Cambios cosméticos.
- Refactor masivo de arquitectura no ligado a seguridad.

Restricciones explícitas del usuario:
- Mantenerse en free tier.
- Probar seguridad de forma controlada.
- La mejora de deck building viene después.

Estrategia recomendada para producción:
- Fase 1: parche mínimo en `main`.
- Fase 2: verificar producción.
- Fase 3: rotar claves comprometidas.
- Fase 4: crear rama nueva (`secure-ai-refactor` o similar).

Plan posterior ya discutido pero NO ejecutar todavía:
- Pasar creación de mazos a pipeline híbrido:
  - lógica determinista fuerte fuera del LLM
  - LLM solo para estrategia/selección fina
- Python fue considerado para futuro, pero no debe entrar antes de cerrar seguridad.

Prompt/instrucción maestra ya definida para GAS:
- En seguridad, GAS debe entender que:
  - Git contiene solo frontend y datos públicos
  - Vercel maneja secrets y endpoints server-side
  - frontend no debe contener claves
  - parche mínimo primero
  - refactor grande después en rama nueva

Temas pendientes al retomar:
1. Definir exactamente qué archivos del repo app cambian para el parche de `main`.
2. Diseñar `/api/build-deck` con contrato compatible con el frontend actual.
3. Mover `geminiService` y `groqService` fuera de cliente.
4. Revisar y neutralizar cualquier otra exposición de secretos.
5. Solo después iniciar arquitectura de deck engine mejorada.

Notas sobre el usuario y preferencias:
- Quiere instrucciones muy exactas y accionables.
- Valora auditoría, arquitectura y planificación.
- Prefiere separar claramente parche urgente vs refactor grande.
- Está manejando varios proyectos a la vez; conviene retomar siempre desde decisiones ya tomadas, no reabrir debates cerrados.

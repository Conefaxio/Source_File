Instrucciones para el usuario

Objetivo inmediato:
- Resolver seguridad primero.
- No mezclar todavía el refactor grande.

Haz esto en este orden:

1. Congelar alcance
- En esta fase solo seguridad.
- No mezclar mejoras de deck builder, prompts o UX.

2. Asumir compromiso de claves
- Las claves hardcodeadas deben considerarse expuestas.
- Después del parche deben rotarse.

3. Configurar Vercel
- Crear Environment Variables:
  - `GEMINI_API_KEY`
  - `GROQ_API_KEY`
- Evitar nombres ambiguos como `API_KEY` si pueden inducir uso incorrecto.

4. Parchear `main`
- Solo cambios mínimos:
  - quitar keys del cliente
  - crear endpoint server-side
  - redirigir frontend a ese endpoint

5. Desplegar y verificar
- Confirmar que la app sigue creando mazos.
- Confirmar que el navegador ya no expone claves.
- Confirmar que no hay llamadas directas a proveedores IA.

6. Rotar claves
- Revocar/generar nuevas claves.

7. Crear rama nueva desde `main`
- Sugerido:
  - `secure-ai-refactor`
  - o `deck-engine-migration`

8. Solo entonces iniciar el refactor grande
- Repartir lógica fuera del LLM.
- Rediseñar creación de mazos.
- Evaluar futura capa más determinista.

Uso de GAS Remix:
- útil como sandbox rápido
- no usarlo como fuente final de cambios
- los cambios definitivos deben quedar en Git

Recordatorio:
- Git: fuente de verdad
- Vercel: runtime publicado + server-side mínimo
- GAS: desarrollo/prototipo

Instrucción final para GAS

Objetivo: ejecutar un parche de seguridad mínimo y controlado sobre la app actual, sin mezclar todavía el refactor grande.

Contexto:
- GAS se usa solo como entorno de desarrollo/prototipado.
- GitHub es la fuente de verdad del código.
- Vercel publica la app desde `main`.
- Debemos mantenernos en free tier.
- La prioridad absoluta es sacar los secretos del cliente.
- No debemos rehacer toda la app en este paso.
- Después del parche de seguridad, trabajaremos en una rama nueva basada en `main`.

Arquitectura objetivo para este parche:
- Frontend publicado en Vercel.
- Las API keys NO deben existir en React cliente, bundle JS, localStorage, Git ni archivos públicos.
- Las llamadas a IA deben salir desde una Vercel Function mínima.
- Los secretos deben vivir solo en Vercel Environment Variables.
- GAS no forma parte del runtime productivo.

Alcance de esta fase:
1. Implementar solo el parche de seguridad.
2. No rediseñar todavía la lógica de deck building.
3. No introducir Python.
4. No introducir backend persistente.
5. No cambiar datasets públicos ni meta snapshots.
6. No romper la UI actual.
7. Mantener el contrato funcional lo más parecido posible al actual.

Cambios requeridos:
1. Eliminar toda API key hardcodeada del cliente.
2. Eliminar toda llamada directa del cliente a Gemini/Groq.
3. Crear una Vercel Function mínima, por ejemplo `/api/build-deck`.
4. Esa función debe:
   - recibir JSON pequeño
   - validar input básico
   - usar variables de entorno seguras
   - llamar al proveedor IA
   - devolver JSON compatible con el frontend actual
5. El frontend debe consumir la Vercel Function y no hablar directo con Gemini/Groq.

Payload esperado del frontend:
{
  "cardName": "string",
  "format": "standard|alchemy|explorer|historic|timeless|brawl",
  "language": "string",
  "provider": "gemini|groq",
  "card": { ...solo si es necesario y manteniéndolo pequeño... },
  "metaDeck": { ...opcional, pequeño... }
}

Restricciones de costo/free tier:
- La función server-side debe ser mínima.
- No cargar datasets grandes en la función.
- No hacer doble salto runtime.
- No hacer procesamiento pesado en server-side todavía.
- No mover aún metaService ni scryfall search al backend.

Estrategia de despliegue:
1. Hacer primero un parche mínimo en `main`.
2. Mantener el cambio acotado a seguridad.
3. Una vez confirmado el parche en producción, la siguiente etapa será en rama nueva.

Reglas duras:
- No dejar secrets en archivos cliente.
- No usar Vite `define` para inyectar secrets al browser.
- No usar variables públicas para claves privadas.
- No mezclar este parche con mejoras grandes de arquitectura o deck building.
- Mantener la app funcional tras el parche.

Entregable de esta fase:
- listado exacto de archivos tocados
- resumen del flujo nuevo
- verificación de que ya no hay keys en cliente
- contrato de request/response de `/api/build-deck`

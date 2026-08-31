# `/.well-known/` servido por nginx

## `assetlinks.json`

Digital Asset Links. Android sólo le saca la barra de URL al TWA si encuentra
acá el fingerprint SHA-256 de la firma del APK, bajo el mismo dominio que la
app abre. Sin esto el APK funciona igual, pero se ve como un navegador con la
barra puesta — que es exactamente lo que la Fase 3 quiere evitar.

Lo sirve nginx y no la app, por tres razones: es una afirmación sobre el
dominio y no contenido de la aplicación, tiene que responder aunque el
dashboard esté caído, y cambiarlo no necesita reconstruir la imagen.

**El fingerprint de acá es un placeholder.** Sale del keystore con el que se
firma el APK:

```bash
keytool -list -v -keystore android.keystore -alias android | grep 'SHA256:'
```

Bubblewrap también lo imprime al terminar `build`, y lo deja en
`assetlinks.json` dentro de su propio directorio de trabajo.

El `package_name` tiene que coincidir exactamente con el `applicationId` que se
le dio a Bubblewrap. Si cambia uno, cambia el otro.

Después de editarlo alcanza con recargar nginx; el archivo está montado, no
copiado dentro de la imagen:

```bash
docker compose exec proxy nginx -s reload
```

## Verificar

```bash
curl -s https://hernol.com.ar/.well-known/assetlinks.json
```

Tiene que devolver 200 con `content-type: application/json`. La herramienta de
Google que valida el par dominio/APK:

https://developers.google.com/digital-asset-links/tools/generator

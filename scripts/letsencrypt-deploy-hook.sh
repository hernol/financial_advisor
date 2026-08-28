#!/bin/sh
# Recarga el proxy cuando certbot renueva un certificado.
#
# Va en /etc/letsencrypt/renewal-hooks/deploy/. Es un directorio drop-in: no
# toca el .conf de renovación existente, que es el mismo que usan postfix y
# dovecot y que no conviene editar.
#
# Sin esto nginx seguiría sirviendo el certificado viejo hasta el próximo
# reinicio del contenedor, que puede no llegar antes del vencimiento.
set -e
if docker ps --format '{{.Names}}' | grep -q '^financial_analyzer_proxy$'; then
    docker exec financial_analyzer_proxy nginx -s reload
    echo "proxy recargado tras renovar $RENEWED_LINEAGE"
fi

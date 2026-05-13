from app.core.database import SessionLocal
from app.models.consent import LegalDocument
from app.services.consent_service import calculate_document_hash
from app.utils.dates import utc_now


TERMS_AND_CONDITIONS_MARKDOWN = """# Términos y condiciones de uso de Labora

Última actualización: 13 de mayo de 2026

El presente documento regula los términos y condiciones de acceso, navegación y uso del sitio web, plataforma y servicios ofrecidos bajo la marca Labora por [nombre legal de la empresa], con NIF/CIF [número], domicilio social en [dirección completa] y correo electrónico de contacto [email de contacto] (en adelante, "Labora").

El acceso, navegación, registro o utilización de los servicios de Labora atribuye la condición de usuario e implica la aceptación plena, expresa y sin reservas de los presentes Términos y Condiciones, así como, en su caso, de la Política de Privacidad y la Política de Cookies.

En caso de no estar de acuerdo con cualquiera de estas disposiciones, el usuario deberá abstenerse de acceder, navegar o utilizar Labora.

## 1. Objeto

Los presentes Términos y Condiciones tienen por objeto regular la relación jurídica entre Labora y los usuarios en relación con el acceso y uso de la plataforma digital, así como de los contenidos, funcionalidades y servicios que se ponen a disposición a través de la misma.

Labora ofrece una plataforma destinada a [describir con precisión el servicio: por ejemplo, la gestión de perfiles profesionales, la conexión entre candidatos y empleadores, la publicación de ofertas de empleo, la gestión de procesos de selección u otros servicios relacionados con la empleabilidad y el reclutamiento].

## 2. Identificación del titular de la plataforma

De conformidad con la normativa aplicable, se informa de que el titular de la plataforma Labora es:

- Titular: [nombre legal de la empresa]
- NIF/CIF: [número]
- Domicilio social: [dirección completa]
- Correo electrónico: [email de contacto]
- Teléfono: [opcional]
- Datos registrales: [opcional, si procede]

## 3. Aceptación y capacidad legal

El acceso o utilización de Labora supone que el usuario ha leído, comprendido y aceptado íntegramente los presentes Términos y Condiciones.

El usuario declara ser mayor de edad y disponer de capacidad legal suficiente para quedar vinculado por estas condiciones. En caso de actuar en nombre de una persona jurídica, empresa u organización, el usuario manifiesta y garantiza que ostenta facultades suficientes para vincular a dicha entidad.

Labora se reserva el derecho de solicitar, en cualquier momento, la acreditación de dicha capacidad o representación.

## 4. Modificación de los términos y condiciones

Labora podrá modificar en cualquier momento los presentes Términos y Condiciones, total o parcialmente, por motivos legales, técnicos, operativos, comerciales o derivados de cambios en los servicios prestados.

Las nuevas versiones serán publicadas en el sitio web y resultarán aplicables desde su fecha de publicación, salvo que se indique expresamente otra fecha de entrada en vigor.

Se recomienda al usuario revisar periódicamente este documento para conocer la versión vigente en cada momento.

## 5. Condiciones de acceso y uso de la plataforma

El acceso a Labora tiene carácter gratuito, sin perjuicio de que determinadas funcionalidades o servicios puedan estar sujetos al pago de una contraprestación, en cuyo caso ello será debidamente informado al usuario con carácter previo a su contratación.

El usuario se compromete a utilizar la plataforma de conformidad con la ley, la buena fe, el orden público, los usos generalmente aceptados y los presentes Términos y Condiciones.

En particular, el usuario se obliga a:

- Facilitar información veraz, exacta, actualizada y completa.
- Utilizar la plataforma de forma diligente, correcta y lícita.
- Custodiar adecuadamente sus credenciales de acceso, en caso de registro.
- No realizar actos que puedan dañar, inutilizar, sobrecargar o deteriorar la plataforma o impedir su normal utilización por parte de otros usuarios.
- No emplear la plataforma con fines fraudulentos, ilícitos, engañosos o no autorizados.

Labora se reserva el derecho de denegar, restringir, suspender o cancelar el acceso a la plataforma a aquellos usuarios que incumplan las presentes condiciones.

## 6. Registro de usuarios y cuenta de acceso

Determinadas funcionalidades de Labora podrán requerir el registro previo del usuario mediante la creación de una cuenta.

En tales supuestos, el usuario será responsable de:

- Mantener la confidencialidad de su nombre de usuario y contraseña.
- No facilitar sus credenciales a terceros.
- Comunicar inmediatamente a Labora cualquier uso no autorizado de su cuenta o cualquier incidente de seguridad del que tenga conocimiento.

El usuario responderá de las actuaciones realizadas a través de su cuenta, salvo que acredite de forma fehaciente que dicho uso se produjo sin su consentimiento y sin negligencia por su parte.

Labora podrá suspender o cancelar cuentas de usuario cuando detecte indicios de uso fraudulento, información falsa, incumplimiento contractual o cualquier conducta contraria a la ley o a estos Términos y Condiciones.

## 7. Naturaleza de los servicios

Labora pone a disposición de los usuarios una plataforma tecnológica de intermediación, gestión, publicación, conexión o apoyo en relación con [describir el servicio exacto].

Salvo que se indique expresamente lo contrario, Labora no garantiza:

- La obtención de empleo, contratación o incorporación laboral.
- La selección de un candidato concreto por parte de terceros.
- La cobertura efectiva de vacantes por parte de empresas usuarias.
- La consecución de resultados concretos derivados del uso de la plataforma.

Labora asume una obligación de medios, no de resultado, respecto del funcionamiento general de la plataforma y de los servicios ofrecidos.

## 8. Obligaciones específicas del usuario

El usuario se compromete expresamente a no:

- Introducir datos falsos, inexactos, desactualizados o que induzcan a error.
- Suplantar la identidad de terceros o utilizar cuentas ajenas sin autorización.
- Publicar, alojar o difundir contenidos ilícitos, ofensivos, difamatorios, discriminatorios, violentos, obscenos o contrarios a derechos fundamentales.
- Vulnerar derechos de propiedad intelectual, industrial, imagen, honor, intimidad o cualquier otro derecho de terceros.
- Introducir virus, malware, bots, scripts automatizados o cualquier otro mecanismo susceptible de alterar la seguridad o el funcionamiento de la plataforma.
- Realizar actividades de scraping, extracción masiva de datos, ingeniería inversa o explotación no autorizada de la plataforma.
- Utilizar Labora para el envío de comunicaciones comerciales no solicitadas, spam o fines distintos de los previstos por la propia naturaleza del servicio.

El usuario responderá frente a Labora y frente a terceros por cualesquiera daños y perjuicios que pudieran derivarse del incumplimiento de estas obligaciones.

## 9. Contenidos y datos facilitados por los usuarios

El usuario podrá incorporar a la plataforma información, documentos, imágenes, textos, ofertas, currículums, candidaturas u otros materiales, según las funcionalidades disponibles.

El usuario garantiza que:

- Es titular legítimo de los contenidos aportados o dispone de autorización suficiente para su utilización.
- Los contenidos son veraces y no vulneran derechos ni intereses legítimos de terceros.
- Su publicación o tratamiento a través de Labora se ajusta a la normativa aplicable.

Mediante la carga o envío de dichos contenidos, el usuario otorga a Labora una licencia no exclusiva, gratuita, mundial y limitada al tiempo necesario para la prestación del servicio, con el único fin de alojar, reproducir, organizar, adaptar técnicamente, mostrar y utilizar dichos contenidos en el marco del funcionamiento ordinario de la plataforma.

Labora se reserva el derecho de retirar, bloquear o no publicar aquellos contenidos que considere contrarios a la ley, a estos Términos y Condiciones o a derechos de terceros.

## 10. Propiedad intelectual e industrial

Todos los derechos de propiedad intelectual e industrial sobre la plataforma, su diseño, código fuente, estructura, bases de datos, textos, imágenes, logotipos, marcas, nombres comerciales, funcionalidades y demás elementos que la integran son titularidad de Labora o de terceros que han autorizado su uso.

En ningún caso el acceso, navegación o utilización de la plataforma implica cesión, transmisión, licencia o renuncia alguna sobre dichos derechos, salvo que se establezca expresamente por escrito.

Queda expresamente prohibida la reproducción, distribución, comunicación pública, transformación, extracción, reutilización o explotación, total o parcial, por cualquier medio, de los contenidos o elementos de Labora sin autorización previa, expresa y por escrito del titular correspondiente.

## 11. Precios, facturación y pagos

En caso de que Labora ofrezca servicios de pago, los precios, tarifas, modalidades de suscripción, condiciones económicas, medios de pago aceptados y, en su caso, impuestos aplicables, se indicarán de forma clara antes de la contratación.

El usuario acepta que:

- Los importes serán exigibles en la forma y plazos indicados en cada momento.
- El impago facultará a Labora para suspender o cancelar el acceso a los servicios contratados.
- Las suscripciones podrán renovarse automáticamente cuando así se indique expresamente durante el proceso de contratación.

Salvo disposición legal en contrario, los importes abonados no serán reembolsables una vez iniciado el servicio correspondiente.

Si Labora no ofrece actualmente servicios de pago al usuario final, esta cláusula podrá mantenerse reservada para futuras funcionalidades o eliminarse en la versión definitiva.

## 12. Disponibilidad y continuidad del servicio

Labora procurará mantener la plataforma accesible y operativa de forma continuada. No obstante, no garantiza la disponibilidad permanente, la ausencia de interrupciones ni la inexistencia de errores.

Labora podrá suspender temporalmente el acceso por razones de mantenimiento, actualización, mejora técnica, seguridad, incidencias operativas o causas ajenas a su control.

En la medida de lo posible, Labora adoptará las medidas razonables para minimizar dichas interrupciones.

## 13. Exclusión de garantías

Salvo en los casos en que la ley disponga expresamente lo contrario, Labora no garantiza:

- La disponibilidad continua e ininterrumpida del sitio web o de la plataforma.
- La ausencia absoluta de errores, incidencias o fallos técnicos.
- La idoneidad de la plataforma para necesidades específicas del usuario.
- La obtención de resultados determinados derivados del uso del servicio.
- La ausencia de virus u otros elementos lesivos, sin perjuicio de la adopción de medidas de seguridad razonables.

El usuario reconoce y acepta que utiliza la plataforma bajo su exclusiva responsabilidad.

## 14. Limitación de responsabilidad

En la máxima medida permitida por la legislación aplicable, Labora no será responsable de los daños y perjuicios directos o indirectos, lucro cesante, pérdida de ingresos, pérdida de oportunidades, pérdida de datos o daños reputacionales que puedan derivarse de:

- El acceso, uso o imposibilidad de uso de la plataforma.
- La interrupción, suspensión, mal funcionamiento o indisponibilidad del servicio.
- Errores u omisiones en los contenidos o informaciones disponibles.
- La actuación de terceros, incluidos empleadores, candidatos, proveedores o sitios enlazados.
- Accesos no autorizados o incidentes de seguridad no imputables directamente a Labora.
- Decisiones adoptadas por el usuario con base en la información obtenida a través de la plataforma.

En caso de que, pese a lo anterior, se declarase la responsabilidad de Labora, dicha responsabilidad quedará limitada, como máximo, al importe efectivamente abonado por el usuario a Labora durante los doce (12) meses inmediatamente anteriores al hecho causante de la reclamación o, en defecto de pago, al mínimo legalmente admisible.

## 15. Enlaces a terceros y servicios externos

La plataforma podrá contener enlaces, integraciones, accesos o referencias a sitios web, plataformas, herramientas o servicios de terceros.

Labora no controla ni supervisa de forma permanente dichos servicios externos y, por tanto, no asume responsabilidad alguna respecto de su contenido, disponibilidad, legalidad, exactitud o políticas aplicables.

El acceso y uso de dichos recursos externos será responsabilidad exclusiva del usuario y quedará sometido a los términos y políticas del tercero correspondiente.

## 16. Protección de datos personales

El tratamiento de los datos personales recabados a través de Labora se regirá por lo dispuesto en la correspondiente Política de Privacidad, que forma parte integrante del marco jurídico aplicable al uso de la plataforma.

El usuario garantiza la exactitud y actualización de los datos facilitados. En caso de proporcionar datos personales de terceros, declara contar con legitimación suficiente para ello y haber cumplido con los deberes de información legalmente exigibles.

## 17. Seguridad

Labora adopta medidas técnicas y organizativas razonables orientadas a proteger la seguridad, integridad y confidencialidad de la información tratada a través de la plataforma.

No obstante, el usuario reconoce que ningún sistema informático ni transmisión a través de Internet puede garantizar una seguridad absoluta. En consecuencia, Labora no puede asegurar la invulnerabilidad total de sus sistemas, si bien se compromete a actuar diligentemente de conformidad con la normativa aplicable.

## 18. Suspensión, cancelación y resolución

Labora podrá suspender, limitar o cancelar, de forma temporal o definitiva, el acceso del usuario a la plataforma en los siguientes supuestos:

- Incumplimiento de los presentes Términos y Condiciones.
- Aportación de información falsa o engañosa.
- Uso fraudulento, abusivo, ilícito o contrario a la finalidad de la plataforma.
- Existencia de riesgos de seguridad, indicios de actividad sospechosa o requerimiento de autoridad competente.

El usuario podrá dejar de utilizar Labora en cualquier momento y, si dispone de cuenta, solicitar su baja a través de los canales habilitados al efecto.

La suspensión o terminación de la relación contractual no afectará a aquellas disposiciones que, por su naturaleza, deban continuar vigentes, incluidas las relativas a propiedad intelectual, limitación de responsabilidad, protección de datos y jurisdicción aplicable.

## 19. Nulidad parcial e interpretación

Si cualquier cláusula de los presentes Términos y Condiciones fuera declarada nula, inválida o inaplicable, total o parcialmente, dicha nulidad no afectará a la validez de las restantes disposiciones, que permanecerán en pleno vigor y efecto.

La cláusula afectada se interpretará o integrará, en la medida de lo posible, de forma coherente con la finalidad perseguida por el texto original.

## 20. Legislación aplicable y jurisdicción competente

Los presentes Términos y Condiciones se regirán e interpretarán de conformidad con la legislación de [país].

Para cuantas controversias pudieran derivarse del acceso, uso, interpretación o ejecución de estos Términos y Condiciones, las partes se someten expresamente a los juzgados y tribunales de [ciudad], salvo que la normativa imperativa en materia de consumo o cualquier otra disposición aplicable establezca un fuero distinto.

## 21. Contacto

Para cualquier consulta, incidencia o comunicación relacionada con los presentes Términos y Condiciones, el usuario podrá dirigirse a:

- [Nombre legal de la empresa]
- [Dirección completa]
- [Correo electrónico de contacto]
- [Teléfono, opcional]
"""


INITIAL_LEGAL_DOCUMENTS = [
    {
        "type": "terms_and_conditions",
        "title": "Términos y condiciones de uso de Labora",
        "slug": "terminos-y-condiciones",
        "version": "2026.05.13",
        "content_markdown": TERMS_AND_CONDITIONS_MARKDOWN,
    },
    {
        "type": "personal_data_processing",
        "title": "Politica de tratamiento de datos personales",
        "slug": "tratamiento-datos-personales",
        "content_markdown": (
            "# Tratamiento de datos personales\n\n"
            "Texto base para autorizacion de tratamiento de datos personales."
        ),
    },
    {
        "type": "sensitive_data_processing",
        "title": "Autorizacion de tratamiento de datos sensibles",
        "slug": "tratamiento-datos-sensibles",
        "content_markdown": (
            "# Tratamiento de datos sensibles\n\n"
            "Texto base para datos laborales, pensionales, juridicos y salariales."
        ),
    },
    {
        "type": "electronic_means",
        "title": "Autorizacion de medios electronicos",
        "slug": "medios-electronicos",
        "content_markdown": (
            "# Medios electronicos\n\n"
            "Texto base para comunicaciones y documentos digitales."
        ),
    },
    {
        "type": "ai_scope_acknowledgement",
        "title": "Alcance del analisis asistido por IA",
        "slug": "alcance-ia",
        "content_markdown": (
            "# Alcance del analisis asistido por IA\n\n"
            "Texto base sobre preanalisis, limitaciones y posible revision profesional."
        ),
    },
]


def seed_initial_legal_documents(version: str = "2026.05.01") -> int:
    db = SessionLocal()
    created_count = 0
    now = utc_now()
    try:
        for item in INITIAL_LEGAL_DOCUMENTS:
            item_version = item.get("version", version)
            exists = (
                db.query(LegalDocument)
                .filter(
                    LegalDocument.type == item["type"],
                    LegalDocument.version == item_version,
                )
                .one_or_none()
            )
            if exists is not None:
                exists.status = "active"
                exists.effective_to = None
                exists.updated_at = now
                _archive_previous_active_documents(
                    db,
                    consent_type=item["type"],
                    exclude_document_id=exists.id,
                    archived_at=now,
                )
                continue
            _archive_previous_active_documents(
                db,
                consent_type=item["type"],
                exclude_document_id=None,
                archived_at=now,
            )
            document = LegalDocument(
                type=item["type"],
                title=item["title"],
                slug=item["slug"],
                content_markdown=item["content_markdown"],
                content_plain_text=None,
                version=item_version,
                hash_sha256=calculate_document_hash(
                    consent_type=item["type"],
                    title=item["title"],
                    version=item_version,
                    content_markdown=item["content_markdown"],
                ),
                status="active",
                is_required=True,
                effective_from=now,
            )
            db.add(document)
            created_count += 1
        db.commit()
        return created_count
    finally:
        db.close()


def _archive_previous_active_documents(
    db,
    *,
    consent_type: str,
    exclude_document_id,
    archived_at,
) -> None:
    query = db.query(LegalDocument).filter(
        LegalDocument.type == consent_type,
        LegalDocument.status == "active",
    )
    if exclude_document_id is not None:
        query = query.filter(LegalDocument.id != exclude_document_id)
    for document in query.all():
        document.status = "archived"
        document.effective_to = archived_at
        document.updated_at = archived_at


if __name__ == "__main__":
    created = seed_initial_legal_documents()
    print(f"Seed de documentos legales completado. Creados: {created}")

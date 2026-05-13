from app.core.database import SessionLocal
from app.models.consent import LegalDocument
from app.services.consent_service import REQUIRED_CONSENT_TYPES, calculate_document_hash
from app.utils.dates import utc_now


LEGAL_DOCUMENT_VERSION = "2026.05.13"


TERMS_AND_CONDITIONS_MARKDOWN = """# TÉRMINOS Y CONDICIONES DE USO DE LABORA

Última actualización: 13 de mayo de 2026

El presente documento regula los términos y condiciones de acceso, navegación y uso del sitio web, plataforma y servicios ofrecidos bajo la marca Labora por [nombre legal de la empresa], con NIF/CIF [número], domicilio social en [dirección completa] y correo electrónico de contacto [email de contacto] (en adelante, “Labora”).

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

- Salvo disposición legal en contrario, los importes abonados no serán reembolsables una vez iniciado el servicio correspondiente.

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

[Nombre legal de la empresa]

[Dirección completa]

[Correo electrónico de contacto]

[Teléfono, opcional]
"""


PERSONAL_DATA_PROCESSING_MARKDOWN = """# POLÍTICA DE PRIVACIDAD Y TRATAMIENTO DE DATOS PERSONALES DE LABORA

Última actualización: [fecha]

En Labora, la privacidad y la protección de los datos personales de nuestros usuarios son una prioridad. La presente Política de Privacidad y Tratamiento de Datos Personales tiene por objeto informar de forma clara, transparente y completa sobre cómo recopilamos, utilizamos, conservamos y protegemos los datos personales de las personas que acceden, navegan, se registran o utilizan el sitio web, la plataforma o los servicios de Labora.

Esta política se aplica a todos los tratamientos de datos personales realizados por [nombre legal de la empresa], con NIF/CIF [número], domicilio social en [dirección completa] y correo electrónico de contacto [email de contacto] (en adelante, “Labora” o el “Responsable del Tratamiento”).

El tratamiento de los datos personales se realiza de conformidad con lo dispuesto en el Reglamento (UE) 2016/679, General de Protección de Datos (RGPD), la Ley Orgánica 3/2018, de Protección de Datos Personales y garantía de los derechos digitales (LOPDGDD) y demás normativa aplicable.

## 1. Responsable del tratamiento

El responsable del tratamiento de los datos personales es:

- Responsable: [nombre legal de la empresa]

- NIF/CIF: [número]

- Domicilio social: [dirección completa]

- Correo electrónico de contacto: [email de contacto]

- Teléfono: [opcional]

- Delegado de Protección de Datos (si aplica): [nombre o correo electrónico del DPO]

## 2. Ámbito de aplicación

La presente Política de Privacidad se aplica al tratamiento de datos personales que Labora realiza a través de:

- El sitio web de Labora.

- Los formularios de contacto, registro o solicitud de información.

- La creación y gestión de cuentas de usuario.

- La contratación o utilización de servicios ofrecidos por la plataforma.

- El envío de comunicaciones comerciales o informativas, cuando proceda.

- La atención a consultas, incidencias o reclamaciones.

- Cualquier otra interacción entre el usuario y Labora por medios electrónicos, telefónicos o presenciales.

## 3. Qué datos personales recopilamos

Labora podrá tratar las siguientes categorías de datos personales, en función de la relación mantenida con el usuario y de los servicios utilizados:

### 3.1. Datos identificativos y de contacto

- Nombre y apellidos.

- Correo electrónico.

- Número de teléfono.

- Dirección postal, en su caso.

- Documento identificativo, cuando sea necesario y legalmente procedente.

### 3.2. Datos de cuenta y acceso

- Nombre de usuario.

- Contraseña cifrada o credenciales de autenticación.

- Historial de acceso, actividad y uso de la cuenta.

### 3.3. Datos profesionales o de perfil

En caso de que la plataforma permita perfiles profesionales, candidaturas o procesos de selección, Labora podrá tratar:

- Currículum vitae.

- Formación académica.

- Experiencia laboral.

- Competencias, habilidades y preferencias profesionales.

- Carta de presentación, portfolio o documentación relacionada.

- Información contenida en ofertas, candidaturas o procesos de selección.

### 3.4. Datos de navegación y uso

- Dirección IP.

- Tipo de dispositivo y navegador.

- Sistema operativo.

- Fecha y hora de acceso.

- Páginas visitadas, clics, sesiones y patrones de navegación.

- Datos recogidos mediante cookies o tecnologías similares, conforme a la Política de Cookies.

### 3.5. Datos derivados de comunicaciones

- Contenido de mensajes enviados a través de formularios, correo electrónico, chat o canales de soporte.

- Información aportada en consultas, incidencias, reclamaciones o solicitudes de ejercicio de derechos.

### 3.6. Datos de facturación y pago

Si existen servicios de pago, Labora podrá tratar:

- Datos de facturación.

- Información fiscal necesaria.

- Datos relativos al estado de pagos, suscripciones o transacciones.

Labora no almacenará datos bancarios completos ni datos de tarjeta más allá de lo estrictamente necesario cuando dichos pagos sean gestionados por proveedores especializados.

## 4. Cómo obtenemos los datos

Los datos personales que trata Labora pueden proceder de las siguientes fuentes:

- Datos facilitados directamente por el usuario al registrarse, completar formularios o utilizar la plataforma.

- Datos generados como consecuencia del uso de la web, cuenta o servicios.

- Datos aportados por terceros autorizados por el usuario o necesarios para la prestación del servicio.

- Datos obtenidos mediante cookies, tecnologías similares o herramientas de analítica, cuando proceda y exista base jurídica para ello.

El usuario garantiza que los datos facilitados son exactos, veraces, completos y actualizados, y se compromete a comunicar cualquier modificación.

## 5. Finalidades del tratamiento

Labora tratará los datos personales para las siguientes finalidades:

### 5.1. Gestión de la relación con el usuario

- Gestionar el alta y mantenimiento de la cuenta de usuario.

- Permitir el acceso a la plataforma y a sus funcionalidades.

- Verificar la identidad del usuario cuando sea necesario.

### 5.2. Prestación de servicios

- Prestar los servicios solicitados por el usuario.

- Gestionar perfiles, publicaciones, solicitudes, procesos o interacciones dentro de la plataforma.

- Facilitar el funcionamiento técnico, operativo y administrativo del servicio.

### 5.3. Atención al usuario

- Responder consultas, solicitudes de información, reclamaciones o incidencias.

- Prestar soporte técnico, administrativo o comercial.

### 5.4. Comunicaciones informativas y comerciales

- Enviar comunicaciones relacionadas con el servicio, la cuenta, cambios en las condiciones legales, incidencias técnicas o avisos relevantes.

- Enviar información comercial, novedades, promociones o contenidos relacionados con Labora, solo cuando exista consentimiento previo o base jurídica suficiente.

### 5.5. Mejora de la plataforma y análisis

- Analizar el uso del sitio web y la plataforma.

- Mejorar la experiencia del usuario, el rendimiento, la seguridad y la calidad de los servicios.

- Elaborar estadísticas agregadas o informes internos, sin identificar personalmente a los usuarios cuando ello sea posible.

### 5.6. Cumplimiento legal

- Dar cumplimiento a obligaciones legales, regulatorias, fiscales, contables o administrativas.

- Atender requerimientos de autoridades públicas, judiciales o administrativas cuando resulte legalmente exigible.

### 5.7. Prevención del fraude y seguridad

- Detectar, prevenir y controlar usos indebidos, actividades fraudulentas, accesos no autorizados o incidentes de seguridad.

- Proteger la integridad de la plataforma, sus usuarios y la información tratada.

## 6. Base jurídica que legitima el tratamiento

Labora tratará los datos personales sobre la base de una o varias de las siguientes legitimaciones:

### 6.1. Ejecución de un contrato o aplicación de medidas precontractuales

Cuando el tratamiento sea necesario para prestar los servicios solicitados, gestionar el registro del usuario, tramitar solicitudes o ejecutar la relación contractual existente.

### 6.2. Consentimiento del interesado

Cuando el usuario haya otorgado su consentimiento expreso, específico, informado e inequívoco para una finalidad concreta, por ejemplo, el envío de comunicaciones comerciales o el uso de determinadas cookies no necesarias.

El usuario podrá retirar su consentimiento en cualquier momento, sin que ello afecte a la licitud del tratamiento previo a su retirada.

### 6.3. Cumplimiento de obligaciones legales

Cuando el tratamiento sea necesario para cumplir obligaciones legales aplicables a Labora, incluidas las de naturaleza fiscal, contable, mercantil, administrativa o de seguridad.

### 6.4. Interés legítimo

Cuando Labora tenga un interés legítimo prevalente y el tratamiento resulte necesario para:

- Garantizar la seguridad de la plataforma.

- Prevenir el fraude.

- Mejorar servicios y procesos internos.

- Gestionar adecuadamente la relación con los usuarios.

- Enviar comunicaciones relacionadas con servicios similares, cuando la normativa lo permita.

En estos casos, Labora realizará la correspondiente ponderación de intereses y respetará en todo momento los derechos y libertades de los interesados.

## 7. Plazo de conservación de los datos

Los datos personales se conservarán durante el tiempo estrictamente necesario para cumplir la finalidad para la que fueron recabados y, posteriormente, durante los plazos legalmente exigidos para atender posibles responsabilidades.

Con carácter general:

- Datos de contacto o consultas: durante el tiempo necesario para atender la solicitud y, después, durante los plazos legales de prescripción aplicables.

- Datos de cuenta de usuario: mientras la cuenta permanezca activa y, posteriormente, bloqueados durante los plazos exigidos por ley.

- Datos vinculados a servicios contratados: durante la vigencia de la relación contractual y los plazos legales posteriores.

- Datos para comunicaciones comerciales: hasta que el usuario retire su consentimiento u ejerza su derecho de oposición.

- Datos de facturación y transacciones: durante los plazos exigidos por la normativa fiscal, contable y mercantil.

- Datos de procesos de selección o perfiles profesionales, si aplica: durante el tiempo necesario para gestionar la relación y, en su caso, por el periodo autorizado o consentido por el usuario.

Una vez finalizados dichos plazos, los datos podrán ser suprimidos o anonimizados de forma segura.

## 8. Destinatarios de los datos

Labora no venderá ni cederá datos personales a terceros, salvo obligación legal o cuando ello sea necesario para la prestación del servicio.

Los datos podrán ser comunicados a:

### 8.1. Proveedores de servicios

Terceros que actúan como encargados del tratamiento y prestan servicios a Labora, tales como:

- Hosting e infraestructura tecnológica.

- Correo electrónico y comunicaciones.

- Soporte técnico.

- Analítica y mejora de servicios.

- Pasarelas de pago.

- Herramientas de atención al cliente.

- Servicios profesionales, jurídicos o administrativos.

En todos los casos, Labora suscribirá con dichos proveedores los correspondientes contratos de encargo del tratamiento cuando legalmente resulte exigible.

### 8.2. Autoridades públicas

Administraciones, jueces, tribunales, fuerzas y cuerpos de seguridad u organismos reguladores cuando exista obligación legal o requerimiento válido.

### 8.3. Terceros vinculados al servicio

Si la naturaleza de la plataforma así lo requiere, determinados datos podrán compartirse con otras entidades o usuarios cuando ello sea imprescindible para la funcionalidad del servicio y conforme a la relación establecida por el usuario.

Ejemplo adaptable si Labora conecta candidatos y empresas:

En caso de que el usuario utilice funcionalidades de candidatura, búsqueda de empleo o interacción profesional, determinados datos de perfil, currículum o candidatura podrán ponerse a disposición de empresas, reclutadores u organizaciones con las que el propio usuario decida interactuar dentro de la plataforma.

Si esta funcionalidad existe, conviene revisar esta cláusula para adaptarla exactamente a vuestro modelo.

## 9. Transferencias internacionales de datos

Con carácter general, Labora procurará que el tratamiento de datos se realice dentro del Espacio Económico Europeo.

No obstante, algunos proveedores tecnológicos pueden estar ubicados fuera del Espacio Económico Europeo o tratar datos desde terceros países. En tales casos, Labora adoptará las garantías adecuadas exigidas por la normativa vigente, incluyendo, cuando proceda:

- Decisiones de adecuación de la Comisión Europea.

- Cláusulas contractuales tipo aprobadas por la Comisión Europea.

- Normas corporativas vinculantes u otros mecanismos legalmente reconocidos.

El usuario podrá solicitar información adicional sobre estas garantías a través del correo de contacto indicado en esta política.

## 10. Derechos de las personas interesadas

El usuario puede ejercer, en cualquier momento y de forma gratuita, los siguientes derechos en materia de protección de datos:

- Derecho de acceso: conocer qué datos personales tratamos y obtener copia de ellos.

- Derecho de rectificación: solicitar la corrección de datos inexactos o incompletos.

- Derecho de supresión: solicitar la eliminación de sus datos cuando ya no sean necesarios o cuando concurra alguna de las circunstancias previstas legalmente.

- Derecho de oposición: oponerse a determinados tratamientos basados en interés legítimo o al envío de comunicaciones comerciales.

- Derecho a la limitación del tratamiento: solicitar que se restrinja temporalmente el uso de sus datos en determinados supuestos.

- Derecho a la portabilidad: recibir sus datos en formato estructurado, de uso común y lectura mecánica, y transmitirlos a otro responsable cuando sea técnicamente posible y legalmente procedente.

- Derecho a retirar el consentimiento: en cualquier momento, cuando el tratamiento se base en el consentimiento del interesado.

- Derecho a no ser objeto de decisiones automatizadas individualizadas, incluida la elaboración de perfiles, cuando proceda legalmente.

Para ejercer estos derechos, el usuario podrá enviar una solicitud a:

[correo electrónico de privacidad o contacto]

La solicitud deberá identificar claramente el derecho que se desea ejercer y podrá requerirse información adicional razonable para verificar la identidad del solicitante.

Asimismo, el usuario tiene derecho a presentar una reclamación ante la Agencia Española de Protección de Datos (AEPD) si considera que el tratamiento de sus datos no se ajusta a la normativa aplicable.

Sitio web de la AEPD: https://www.aepd.es

## 11. Comunicaciones comerciales

Labora solo enviará comunicaciones comerciales por medios electrónicos cuando:

- El usuario lo haya consentido expresamente; o

- Exista una relación contractual previa y se trate de comunicaciones sobre productos o servicios similares, en los términos permitidos por la normativa aplicable.

El usuario podrá darse de baja de estas comunicaciones en cualquier momento mediante el enlace habilitado en cada comunicación o solicitándolo a través del correo de contacto.

## 12. Datos de menores de edad

Labora no está dirigida a menores de edad, salvo que se indique expresamente lo contrario.

Con carácter general, no se tratarán datos personales de menores de 14 años sin el consentimiento expreso de sus padres, madres o representantes legales, de conformidad con la normativa española aplicable.

Si Labora detecta que ha recopilado datos personales de un menor sin base legítima suficiente, podrá proceder a su supresión.

Si consideras que un menor nos ha facilitado datos personales indebidamente, puedes ponerte en contacto con nosotros a través de [correo de contacto].

## 13. Seguridad de la información

Labora aplica medidas técnicas y organizativas apropiadas para garantizar un nivel de seguridad adecuado al riesgo, con el fin de proteger los datos personales frente a:

- Acceso no autorizado.

- Alteración, pérdida o destrucción.

- Divulgación indebida.

- Uso ilícito o fraudulento.

No obstante, el usuario reconoce que ningún sistema tecnológico es completamente infalible, por lo que Labora no puede garantizar una seguridad absoluta, aunque sí actúa con la diligencia exigida por la normativa vigente.

## 14. Redes sociales y servicios de terceros

Labora puede mantener presencia en redes sociales o integrar herramientas, enlaces o servicios de terceros. La interacción del usuario con dichas plataformas externas se regirá por sus propias políticas de privacidad y condiciones de uso.

Labora no se hace responsable del tratamiento de datos realizado por dichas plataformas cuando actúan como responsables independientes.

## 15. Cookies y tecnologías similares

El uso de cookies y tecnologías similares se regula de forma específica en la correspondiente Política de Cookies.

Cuando sea necesario, Labora solicitará el consentimiento del usuario para la instalación de cookies no esenciales, de conformidad con la normativa vigente.

## 16. Cambios en esta política de privacidad

Labora podrá modificar la presente Política de Privacidad para adaptarla a cambios normativos, jurisprudenciales, técnicos, organizativos o derivados de la evolución de los servicios ofrecidos.

En caso de modificación, la nueva versión será publicada en el sitio web con indicación de la fecha de última actualización.

Se recomienda al usuario revisar esta política periódicamente.

## 17. Contacto

Para cualquier consulta relacionada con esta Política de Privacidad o con el tratamiento de datos personales, puedes contactar con:

[nombre legal de la empresa]

[dirección completa]

[correo electrónico de contacto]

[teléfono, opcional]

[correo del Delegado de Protección de Datos, si aplica]
"""


SENSITIVE_DATA_PROCESSING_MARKDOWN = """# AUTORIZACIÓN EXPRESA PARA EL TRATAMIENTO DE CATEGORÍAS ESPECIALES DE DATOS PERSONALES

Responsable del tratamiento: [Nombre legal de la empresa]

NIF/CIF: [número]

Domicilio social: [dirección completa]

Correo electrónico de contacto: [email de contacto]

En cumplimiento de lo dispuesto en el Reglamento (UE) 2016/679, General de Protección de Datos (RGPD), la Ley Orgánica 3/2018, de Protección de Datos Personales y garantía de los derechos digitales (LOPDGDD) y demás normativa aplicable, se informa al interesado de que los datos personales que facilite y que pertenezcan a categorías especiales de datos serán tratados por [nombre de la empresa] de conformidad con las siguientes condiciones:

## 1. Finalidad del tratamiento

Los datos personales especialmente protegidos o pertenecientes a categorías especiales de datos serán tratados con la finalidad de:

[describir de forma concreta, específica y legítima la finalidad]

Ejemplos:

- gestionar adaptaciones o necesidades específicas derivadas del estado de salud del interesado;

- tramitar procesos de selección inclusivos o ajustes razonables;

- verificar requisitos legales o contractuales vinculados al servicio;

- gestionar situaciones relacionadas con discapacidad, incapacidad o necesidades especiales debidamente justificadas.

## 2. Categorías de datos objeto de tratamiento

Podrán ser objeto de tratamiento, exclusivamente cuando resulte necesario para la finalidad indicada, las siguientes categorías especiales de datos:

[indicar cuáles]

Ejemplos:

- datos relativos a la salud;

- grado de discapacidad;

- certificados médicos o informes justificativos;

- otros datos especialmente protegidos estrictamente necesarios para la finalidad informada.

## 3. Base jurídica del tratamiento

La base jurídica del tratamiento será el consentimiento explícito del interesado, de conformidad con el artículo 9.2.a) del RGPD, sin perjuicio de cualquier otra base legitimadora que pudiera resultar aplicable conforme a la normativa vigente.

## 4. Destinatarios de los datos

Los datos no serán cedidos a terceros, salvo obligación legal o cuando sea necesario para la prestación del servicio y exista base jurídica suficiente para ello.

Podrán tener acceso a los datos proveedores que actúen como encargados del tratamiento, con los que el Responsable haya suscrito los correspondientes contratos conforme al artículo 28 del RGPD.

## 5. Conservación de los datos

Los datos serán conservados durante el tiempo estrictamente necesario para cumplir la finalidad para la que fueron recabados y, posteriormente, durante los plazos legalmente exigibles para la atención de posibles responsabilidades.

## 6. Derechos del interesado

El interesado podrá ejercer en cualquier momento sus derechos de:

- acceso,

- rectificación,

- supresión,

- oposición,

- limitación del tratamiento,

- portabilidad,

- retirada del consentimiento, en cualquier momento, sin afectar a la licitud del tratamiento previo a su retirada.

Para ello, podrá dirigirse por escrito a [correo electrónico / dirección postal], acreditando su identidad.

Asimismo, podrá presentar una reclamación ante la Agencia Española de Protección de Datos (AEPD) si considera que el tratamiento no se ajusta a la normativa aplicable.

## 7. Consentimiento expreso

Mediante la aceptación del presente documento en la plataforma, el/la interesado/a:

- declara haber sido informado/a de manera clara, expresa y comprensible sobre el tratamiento de sus categorías especiales de datos personales;

- manifiesta que los datos facilitados son adecuados, pertinentes y limitados a lo necesario en relación con la finalidad indicada;

- y otorga su consentimiento explícito, libre, específico, informado e inequívoco para que [nombre de la empresa] trate dichos datos conforme a lo establecido en el presente documento.
"""


ELECTRONIC_MEANS_MARKDOWN = """# AUTORIZACIÓN PARA EL USO DE MEDIOS ELECTRÓNICOS EN LAS COMUNICACIONES

Responsable del tratamiento: [Nombre legal de la empresa]

NIF/CIF: [número]

Domicilio social: [dirección completa]

Correo electrónico de contacto: [email]

En cumplimiento de lo dispuesto en el Reglamento (UE) 2016/679 (RGPD), la Ley Orgánica 3/2018 (LOPDGDD) y la normativa aplicable en materia de servicios de la sociedad de la información y comunicaciones electrónicas, se informa al interesado de que [nombre de la empresa] podrá comunicarse con él/ella a través de medios electrónicos, en los términos que se indican a continuación:

## 1. Objeto de la autorización

El/la interesado/a autoriza expresamente a [nombre de la empresa] a remitirle comunicaciones mediante medios electrónicos con la finalidad de gestionar la relación existente entre las partes.

## 2. Medios autorizados

Las comunicaciones podrán realizarse a través de los siguientes canales:

- correo electrónico,

- SMS,

- llamada telefónica automatizada o manual,

- mensajería instantánea (por ejemplo, WhatsApp o medios equivalentes),

- plataformas o portales digitales habilitados por la entidad.

## 3. Finalidad de las comunicaciones

Las comunicaciones electrónicas tendrán por finalidad:

[describir la finalidad concreta]

Ejemplos:

- enviar avisos, recordatorios o notificaciones relacionadas con el servicio;

- remitir documentación, actualizaciones del expediente o solicitudes de información;

- gestionar citas, entrevistas, incidencias o seguimiento de trámites;

- facilitar información operativa vinculada a la relación contractual o precontractual.

## 4. Alcance de la autorización

La presente autorización comprende exclusivamente las comunicaciones necesarias o vinculadas a la gestión de la relación con [nombre de la empresa].

En caso de que la entidad desee remitir comunicaciones comerciales, promocionales o publicitarias, estas deberán contar, cuando legalmente proceda, con el consentimiento específico correspondiente o con otra base legitimadora válida.

## 5. Base jurídica

La base jurídica del tratamiento de los datos de contacto y del envío de comunicaciones por medios electrónicos será:

- la ejecución de la relación precontractual o contractual, cuando las comunicaciones sean necesarias para su desarrollo; y/o

- el consentimiento del interesado, cuando sea legalmente exigible.

## 6. Datos utilizados

Para el envío de dichas comunicaciones podrán utilizarse los siguientes datos:

- nombre y apellidos,

- dirección de correo electrónico,

- número de teléfono móvil o fijo,

- identificadores de usuario en plataformas o canales digitales facilitados por el interesado.

## 7. Destinatarios y acceso a los datos

Los datos no serán cedidos a terceros, salvo obligación legal o cuando resulte necesario para la prestación del servicio mediante proveedores que actúen como encargados del tratamiento, con las debidas garantías legales.

## 8. Conservación

Los datos se conservarán durante el tiempo necesario para la finalidad para la que fueron recabados y, posteriormente, durante los plazos legalmente exigibles.

## 9. Derechos del interesado

El/la interesado/a podrá ejercer sus derechos de:

- acceso,

- rectificación,

- supresión,

- oposición,

- limitación del tratamiento,

- portabilidad,

- retirada del consentimiento, cuando proceda.

Para ello, podrá dirigirse a [correo electrónico / dirección postal], acreditando su identidad.

Asimismo, podrá presentar una reclamación ante la Agencia Española de Protección de Datos (AEPD) si considera que sus derechos no han sido debidamente atendidos.

## 10. Declaración de consentimiento

Mediante la aceptación del presente documento en la plataforma, el/la interesado/a declara que:

- ha sido informado/a de manera clara, comprensible y suficiente sobre el uso de medios electrónicos para las comunicaciones con [nombre de la empresa];

- autoriza expresamente el uso de los canales electrónicos facilitados para las finalidades indicadas;

- conoce que podrá revocar esta autorización en cualquier momento, cuando la base jurídica sea el consentimiento, sin efectos retroactivos sobre las actuaciones ya realizadas.
"""


AI_SCOPE_ACKNOWLEDGEMENT_MARKDOWN = """# DOCUMENTO DE ALCANCE DEL ANÁLISIS ASISTIDO POR IA

## 1. Identificación del documento

Título: Documento de alcance del análisis asistido por inteligencia artificial

Entidad responsable: [Nombre de la empresa / organización]

Versión: [número de versión]

Fecha de entrada en vigor: [dd/mm/aaaa]

Área responsable: [Departamento / Dirección / Unidad]

Aprobado por: [Nombre / cargo]

## 2. Objeto

El presente documento tiene por objeto definir el alcance, finalidad, límites, condiciones de uso y controles aplicables al uso de sistemas de inteligencia artificial (IA) como herramienta de apoyo en procesos de análisis desarrollados por [nombre de la entidad].

A través de este documento se establece qué funciones puede desempeñar la IA, en qué fases del proceso puede intervenir, qué tareas quedan excluidas, qué nivel de supervisión humana resulta exigible y cuáles son las garantías mínimas de uso responsable, trazable y conforme con la normativa aplicable.

## 3. Finalidad del análisis asistido por IA

La utilización de herramientas de IA tendrá como finalidad principal apoyar, agilizar, estructurar o complementar tareas de análisis, con el objetivo de:

- mejorar la eficiencia operativa;

- facilitar el tratamiento y organización de información;

- detectar patrones, incidencias o elementos relevantes;

- generar resúmenes, clasificaciones o propuestas preliminares;

- reforzar la consistencia metodológica del análisis;

- reducir tiempos de revisión en tareas repetitivas o de alto volumen.

En todo caso, el uso de IA deberá estar orientado a fines legítimos, específicos y previamente definidos, y no podrá utilizarse de forma indeterminada o incompatible con la finalidad inicial del tratamiento o del proceso analítico.

## 4. Definición de análisis asistido por IA

A los efectos de este documento, se entenderá por análisis asistido por IA aquel proceso en el que una herramienta o sistema basado en inteligencia artificial:

- procesa información o datos facilitados por la entidad;

- genera resultados, sugerencias, clasificaciones, resúmenes, valoraciones preliminares o patrones de interés;

- actúa como soporte para la actividad humana;

- no opera, por defecto, como mecanismo autónomo e incuestionable de decisión final.

La salida generada por la IA tendrá carácter auxiliar, orientativo o de apoyo, salvo que una norma interna específica regule de manera expresa otro grado de automatización y se hayan implantado las garantías legales y organizativas correspondientes.

## 5. Ámbito de aplicación

Este documento será de aplicación a todos aquellos procesos, áreas, sistemas, equipos y personas de [nombre de la entidad] que utilicen herramientas de IA para asistir tareas de análisis, incluyendo, entre otros, los siguientes ámbitos:

- análisis documental;

- clasificación y priorización de información;

- revisión preliminar de expedientes;

- extracción de datos relevantes;

- identificación de coincidencias, patrones o alertas;

- apoyo a procesos de evaluación técnica o administrativa;

- asistencia en auditorías, revisiones internas o controles de calidad;

- apoyo a procesos de selección, validación, seguimiento o evaluación, en su caso.

También se aplicará a proveedores o terceros que presten servicios para la entidad cuando utilicen sistemas de IA por cuenta de esta y en relación con procesos incluidos en este alcance.

## 6. Alcance funcional

6.1 Actuaciones comprendidas

La IA podrá utilizarse, entre otras, para las siguientes funciones:

- ordenar, clasificar o etiquetar información;

- resumir documentación o grandes volúmenes de contenido;

- identificar elementos coincidentes con criterios previamente definidos;

- detectar posibles anomalías, incoherencias o datos incompletos;

- proponer categorías, prioridades o agrupaciones;

- extraer información estructurada de documentos o registros;

- generar informes preliminares, borradores técnicos o síntesis operativas;

- asistir en la comparación entre datos, documentos o perfiles;

- apoyar la detección temprana de riesgos o incidencias.

6.2 Naturaleza del resultado

Los resultados generados por la IA podrán consistir en:

- recomendaciones;

- puntuaciones o clasificaciones internas;

- resúmenes;

- propuestas de priorización;

- alertas;

- borradores de análisis;

- sugerencias de revisión.

Estos resultados no deberán interpretarse automáticamente como hechos concluyentes ni como decisión definitiva, salvo habilitación expresa y conforme a la normativa aplicable.

## 7. Límites del alcance

Quedan fuera del alcance permitido de la IA, salvo regulación interna específica y base jurídica suficiente, las siguientes actuaciones:

- adopción de decisiones finales exclusivamente automatizadas con efectos jurídicos o impacto significativo sobre personas, cuando no esté legalmente habilitado;

- elaboración de perfiles prohibidos o discriminatorios;

- tratamiento de datos no pertinentes, excesivos o desvinculados de la finalidad del análisis;

- uso de información obtenida por medios no autorizados;

- incorporación de datos especialmente sensibles sin evaluación previa de necesidad, proporcionalidad y base legitimadora;

- utilización de resultados de IA sin validación humana cuando dicha validación resulte exigible;

- uso de herramientas no aprobadas por la entidad;

- reutilización de datos o resultados para finalidades distintas a las autorizadas.

## 8. Supervisión humana

El uso de IA en procesos de análisis deberá estar sometido a una supervisión humana adecuada, real y efectiva, especialmente cuando los resultados puedan influir en evaluaciones, decisiones, priorizaciones o propuestas de actuación.

La supervisión humana implicará, al menos:

- revisión razonable de los resultados generados por la IA;

- capacidad de cuestionar, corregir o descartar dichos resultados;

- intervención de personal competente en la interpretación de la salida;

- verificación adicional cuando existan dudas, incoherencias o riesgo de sesgo;

- responsabilidad humana sobre la decisión o validación final, cuando proceda.

La supervisión no podrá ser meramente formal o automática, sino proporcional al riesgo, impacto y relevancia del proceso en el que interviene la IA.

## 9. Datos e información objeto de análisis

La IA solo podrá operar sobre datos o información que cumplan cumulativamente las siguientes condiciones:

- hayan sido obtenidos de forma lícita;

- sean adecuados, pertinentes y limitados a la finalidad perseguida;

- presenten un nivel razonable de calidad y actualización;

- puedan ser tratados de acuerdo con la normativa vigente y las políticas internas de la entidad;

- no excedan del nivel de acceso autorizado por perfil o función.

Cuando el análisis pueda incluir datos personales, deberá respetarse en todo momento la normativa aplicable en materia de protección de datos y seguridad de la información.

## 10. Criterios de uso permitido

El uso de IA en el análisis solo será admisible cuando concurran los siguientes requisitos:

- existencia de una finalidad concreta y legítima;

- definición previa del proceso y del uso esperado de la herramienta;

- proporcionalidad entre el uso de la IA y el objetivo perseguido;

- adopción de medidas de control, revisión y trazabilidad;

- evaluación previa de riesgos cuando la naturaleza del proceso así lo requiera;

- utilización de herramientas autorizadas por la entidad;

- información suficiente a las personas afectadas, cuando sea legal o contractualmente exigible.

## 11. Exclusiones y cautelas específicas

La entidad reconoce que los sistemas de IA pueden presentar limitaciones, entre ellas:

- errores de interpretación;

- omisiones o resultados incompletos;

- sesgos derivados del modelo, de los datos o del diseño del proceso;

- respuestas plausibles pero incorrectas;

- falta de contexto suficiente;

- variabilidad en la salida generada;

- dificultades de explicación detallada en determinados modelos.

Por ello, los resultados obtenidos mediante IA no deberán utilizarse de manera aislada cuando puedan generar un riesgo relevante, afectar derechos o producir consecuencias significativas para terceros.

## 12. Responsabilidades

12.1 De la entidad

Corresponde a [nombre de la entidad]:

- autorizar las herramientas de IA admisibles;

- definir los casos de uso permitidos;

- establecer controles y protocolos de supervisión;

- garantizar medidas de seguridad adecuadas;

- formar al personal implicado;

- revisar periódicamente la adecuación del sistema y del proceso.

12.2 Del personal usuario

Corresponde al personal que utilice herramientas de IA:

- emplearlas únicamente dentro del alcance autorizado;

- no introducir información no permitida o innecesaria;

- revisar críticamente los resultados obtenidos;

- documentar incidencias, errores o anomalías relevantes;

- escalar dudas o situaciones de riesgo al área competente;

- respetar la normativa interna, de confidencialidad y protección de datos.

12.3 De proveedores o terceros

Los proveedores o terceros que intervengan en estos procesos deberán:

- actuar conforme a las instrucciones de la entidad;

- ofrecer garantías suficientes de seguridad, confidencialidad y cumplimiento;

- no reutilizar la información para fines propios no autorizados;

- facilitar, cuando proceda, niveles adecuados de trazabilidad, control y soporte.

## 13. Trazabilidad y documentación

Los procesos de análisis asistido por IA deberán permitir, en la medida de lo posible y según la tecnología empleada, un nivel razonable de trazabilidad, incluyendo:

- identificación de la herramienta o sistema utilizado;

- finalidad del análisis realizado;

- fecha y contexto de uso;

- tipo de datos o documentos analizados;

- persona responsable de la revisión;

- resultado generado y decisión adoptada, en su caso;

- incidencias detectadas durante el proceso.

La documentación asociada deberá conservarse conforme a los criterios internos de conservación, seguridad y cumplimiento normativo.

## 14. Seguridad, confidencialidad y protección de datos

Toda utilización de IA deberá realizarse bajo criterios de:

- confidencialidad;

- integridad;

- disponibilidad;

- minimización de datos;

- control de accesos;

- uso restringido a entornos autorizados;

- prevención de accesos no autorizados, fuga de información o reutilización indebida.

Cuando el análisis afecte a datos personales, deberá contarse con la base legitimadora correspondiente y, cuando proceda, con evaluaciones o medidas adicionales derivadas del nivel de riesgo.

## 15. Prohibición de dependencia exclusiva del sistema

La entidad no basará de forma exclusiva y acrítica su análisis en la salida de un sistema de IA cuando:

- exista riesgo de error material;

- puedan verse afectados derechos, intereses o expectativas legítimas de personas físicas o jurídicas;

- el resultado requiera interpretación técnica, jurídica, profesional o contextual;

- la normativa exija intervención humana;

- concurran elementos cualitativos que el sistema no pueda valorar adecuadamente.

La IA debe entenderse como una herramienta de apoyo y no como un sustituto automático del juicio profesional, salvo en tareas estrictamente mecánicas y de bajo riesgo previamente autorizadas.

## 16. Revisión periódica del alcance

El presente documento deberá revisarse de forma periódica y, en todo caso, cuando concurra alguna de las siguientes circunstancias:

- incorporación de nuevas herramientas de IA;

- ampliación o modificación de casos de uso;

- cambios normativos relevantes;

- detección de incidencias, sesgos o fallos significativos;

- cambios sustanciales en la naturaleza de los datos tratados;

- resultados de auditorías internas o externas.

Toda modificación del alcance deberá aprobarse formalmente y quedar debidamente documentada.

## 17. Aceptación y cumplimiento

El uso de herramientas de IA en procesos de análisis dentro de [nombre de la entidad] implicará la obligación de respetar íntegramente lo dispuesto en el presente documento, así como en el resto de políticas internas aplicables en materia de seguridad de la información, protección de datos, cumplimiento normativo, ética y uso responsable de tecnologías.

El incumplimiento de estas directrices podrá dar lugar a la adopción de medidas internas, correctoras o disciplinarias, sin perjuicio de otras responsabilidades legales que pudieran derivarse.
"""


INITIAL_LEGAL_DOCUMENTS = [
    {
        "type": "terms_and_conditions",
        "title": "Términos y condiciones de uso de Labora",
        "slug": "terminos-y-condiciones",
        "version": LEGAL_DOCUMENT_VERSION,
        "content_markdown": TERMS_AND_CONDITIONS_MARKDOWN,
    },
    {
        "type": "personal_data_processing",
        "title": "Política de privacidad y tratamiento de datos personales de Labora",
        "slug": "politica-privacidad-tratamiento-datos-personales",
        "version": LEGAL_DOCUMENT_VERSION,
        "content_markdown": PERSONAL_DATA_PROCESSING_MARKDOWN,
    },
    {
        "type": "sensitive_data_processing",
        "title": "Autorización expresa para el tratamiento de categorías especiales de datos personales",
        "slug": "tratamiento-categorias-especiales-datos-personales",
        "version": LEGAL_DOCUMENT_VERSION,
        "content_markdown": SENSITIVE_DATA_PROCESSING_MARKDOWN,
    },
    {
        "type": "electronic_means",
        "title": "Autorización para el uso de medios electrónicos en las comunicaciones",
        "slug": "medios-electronicos-comunicaciones",
        "version": LEGAL_DOCUMENT_VERSION,
        "content_markdown": ELECTRONIC_MEANS_MARKDOWN,
    },
    {
        "type": "ai_scope_acknowledgement",
        "title": "Documento de alcance del análisis asistido por IA",
        "slug": "alcance-analisis-asistido-ia",
        "version": LEGAL_DOCUMENT_VERSION,
        "content_markdown": AI_SCOPE_ACKNOWLEDGEMENT_MARKDOWN,
    },
]


def seed_initial_legal_documents(version: str = "2026.05.13") -> int:
    db = SessionLocal()
    created_count = 0
    now = utc_now()
    try:
        _archive_documents_outside_required_types(db, archived_at=now)
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
                exists.title = item["title"]
                exists.slug = item["slug"]
                exists.content_markdown = item["content_markdown"]
                exists.content_plain_text = None
                exists.hash_sha256 = calculate_document_hash(
                    consent_type=item["type"],
                    title=item["title"],
                    version=item_version,
                    content_markdown=item["content_markdown"],
                )
                exists.status = "active"
                exists.is_required = True
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


def _archive_documents_outside_required_types(db, *, archived_at) -> None:
    for document in (
        db.query(LegalDocument)
        .filter(
            LegalDocument.status == "active",
            LegalDocument.type.notin_(REQUIRED_CONSENT_TYPES),
        )
        .all()
    ):
        document.status = "archived"
        document.effective_to = archived_at
        document.updated_at = archived_at


if __name__ == "__main__":
    created = seed_initial_legal_documents()
    print(f"Seed de documentos legales completado. Creados: {created}")

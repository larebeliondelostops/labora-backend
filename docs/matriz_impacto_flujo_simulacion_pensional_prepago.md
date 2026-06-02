# Matriz de impacto - flujo simulacion pensional antes de pago

Fecha: 2026-06-01

Objetivo: adaptar el backend para que la simulacion pensional preliminar sea visible antes del pago y el pago desbloquee solo capacidades juridicas documentales.

| Elemento | Tipo | Uso actual | Decision | Accion | Riesgo | Migracion |
|---|---|---|---|---|---|---|
| `cases` | tabla/modelo | Expedientes y maquina de estados | conservar/adaptar | agregar regimen, objetivo, situacion y flags `has_free_simulation`, `paid_legal_document_unlocked` | medio | schema |
| `case_status_history` | tabla | Trazabilidad de estados | conservar | registrar estados nuevos sin borrar historico | bajo | no |
| `documents` / `document_types` | tablas/modelos | Carga documental actual | conservar/adaptar | sembrar tipos documentales MVP y complementarios; historia laboral obligatoria para simulacion | medio | backfill/seed |
| `document_validations` | tabla/modelo | Validacion documental actual | conservar/adaptar | reutilizar para validar PDF y advertencias OCR | bajo | no |
| `document_extractions` | tabla nueva | Extraccion estructurada por documento | migrar | crear tabla separada para salida fija IA/OCR con confianza | bajo | schema |
| `pension_affiliate_profiles` | tabla nueva | No existia perfil pensional RAIS estructurado | migrar | persistir edad, semanas, saldo, IBC base, regimen y faltantes | medio | schema |
| `pension_monthly_contributions` | tabla nueva | No existia consolidado mensual RAIS | migrar | guardar multiples aportantes por mes con IBC, dias, semanas y advertencias | medio | schema |
| `pension_legal_parameters` | tabla nueva | Parametros legales no deben quemarse en codigo | migrar | crear tabla configurable de SMMLV, topes y edades | medio | schema |
| `pension_simulation_assumptions` | tabla nueva | Supuestos del usuario/calculo | migrar | guardar supuestos usados por simulacion | bajo | schema |
| `pension_simulations` | tabla nueva | Resultado preliminar antes de pago | migrar | crear simulacion visible antes de pago con disclaimer/version | bajo | schema |
| `pension_simulation_scenarios` | tabla nueva | Escenarios deterministas | migrar | crear escenarios conservador/base/optimista con formula trace | bajo | schema |
| `pension_alerts` | tabla nueva | Alertas de diagnostico | migrar | persistir riesgos, brechas y sugerencias | bajo | schema |
| `legal_routes` | tabla nueva | Ruta juridica sugerida desde simulacion | migrar | sugerir ruta, plantilla y documentos requeridos | bajo | schema |
| `legal_template_catalog` | tabla nueva | Catalogo inicial de plantillas por clave | migrar | sembrar claves RAIS iniciales, sin reemplazar `legal_templates` existente | bajo | schema/seed |
| `orders` / `payments` | tablas/modelos | Pagos actuales de desbloqueo | conservar/adaptar | aceptar `LEGAL_DRAFT_GENERATION` y mantener `FULL_ANALYSIS_UNLOCK` como legado compatible | alto | no/schema existente |
| `case_entitlements` | tabla nueva | Capacidades desbloqueadas por caso | migrar | crear `free_pension_simulation` y `legal_draft_generation` | medio | schema |
| `paywalls` / `preview_results` | tablas/modelos | Paywall de analisis completo | conservar/adaptar | crear paywall especifico para borrador juridico sin bloquear simulacion | medio | no |
| `/cases/{caseId}/preview` | endpoint | Vista previa/paywall anterior | deprecar gradualmente | mantener por compatibilidad; nuevo flujo debe consumir `/pension/simulations/latest` | alto | no |
| `/cases/{caseId}/paywall` | endpoint | Bloqueo de analisis completo | deprecar gradualmente | mantener legado; agregar `/paywall/legal-draft` | alto | no |
| `/cases/{caseId}/orders` | endpoint | Orden para `FULL_ANALYSIS_UNLOCK` | conservar/adaptar | aceptar `LEGAL_DRAFT_GENERATION` y delegar al flujo juridico | medio | no |
| `/payments/checkout` / webhook | endpoints | Checkout y aprobacion de pago | conservar/adaptar | al aprobar `LEGAL_DRAFT_GENERATION`, crear entitlement y no desbloquear analisis completo | alto | no |
| `LegalActionService` | servicio | Generacion de acciones con analisis completo | conservar/adaptar | reconocer entitlement `legal_draft_generation` como pago valido, sin romper pagos historicos | medio | no |
| `FullAnalysisService`, `ReportService`, `DeliveryService` | servicios | Flujo pago de analisis completo | deprecar gradualmente | conservar por compatibilidad; no usarlos como requisito de simulacion gratuita | alto | no |
| Jobs existentes | workers | Analisis/documentos/reportes | conservar/adaptar | nuevos jobs recomendados se agregaran sin borrar workers actuales | medio | pendiente |

No se elimina ninguna tabla, columna, endpoint, worker ni dato historico en esta migracion. Cualquier limpieza futura debe ser una migracion `cleanup` separada con analisis de dependencias, respaldo y validacion de frontend/backoffice/reportes.


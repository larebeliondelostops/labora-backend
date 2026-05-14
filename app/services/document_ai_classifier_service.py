from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentClassificationResult:
    document_type_code: str
    confidence: float
    reason: str
    signals: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    requires_human_review: bool = False


class DocumentAiClassifierService:
    """Deterministic placeholder for the future documental AI provider."""

    SIGNALS: tuple[tuple[str, str, list[str]], ...] = (
        ("historia_laboral", "historia laboral", ["historia laboral"]),
        ("historia_laboral", "semanas", ["semanas cotizadas"]),
        ("historia_laboral", "periodos laborales", ["periodos laborales"]),
        ("cedula", "cedula", ["documento de identidad"]),
        ("cedula", "cédula", ["documento de identidad"]),
        ("resolucion_pensional", "resolucion", ["resolucion pensional"]),
        ("resolucion_pensional", "resolución", ["resolucion pensional"]),
        ("certificacion_laboral", "certificacion laboral", ["certificacion laboral"]),
        ("certificacion_laboral", "certificación laboral", ["certificacion laboral"]),
        ("desprendible_nomina", "nomina", ["desprendible de nomina"]),
        ("desprendible_nomina", "nómina", ["desprendible de nomina"]),
        ("acto_administrativo", "acto administrativo", ["acto administrativo"]),
        ("certificado_docente", "docente", ["documento docente"]),
        ("acta_posesion", "acta de posesion", ["acta de posesion"]),
        ("acta_posesion", "acta de posesión", ["acta de posesion"]),
        ("respuesta_fondo", "colpensiones", ["fondo pensional"]),
        ("respuesta_fondo", "proteccion", ["fondo pensional"]),
        ("respuesta_fondo", "porvenir", ["fondo pensional"]),
        ("sentencia_previa", "sentencia", ["sentencia previa"]),
        ("tutela_previa", "tutela", ["tutela previa"]),
    )

    def classify(
        self,
        *,
        filename: str,
        extracted_text: str | None,
        mime_type: str,
    ) -> DocumentClassificationResult:
        haystack = f"{filename}\n{extracted_text or ''}".lower()
        scores: dict[str, float] = {}
        signals: dict[str, list[str]] = {}
        for code, needle, signal_items in self.SIGNALS:
            if needle in haystack:
                scores[code] = scores.get(code, 0.0) + 0.35
                signals.setdefault(code, []).extend(signal_items)

        if not scores:
            fallback_confidence = 0.35 if mime_type == "application/pdf" else 0.25
            return DocumentClassificationResult(
                document_type_code="otro_soporte",
                confidence=fallback_confidence,
                reason="No se detectaron senales suficientes para clasificar con certeza.",
                requires_human_review=True,
            )

        code, raw_score = max(scores.items(), key=lambda item: item[1])
        confidence = min(0.95, 0.55 + raw_score)
        return DocumentClassificationResult(
            document_type_code=code,
            confidence=confidence,
            reason="Clasificacion preliminar basada en nombre de archivo y texto extraible.",
            signals=sorted(set(signals.get(code, []))),
            requires_human_review=confidence < 0.8,
        )

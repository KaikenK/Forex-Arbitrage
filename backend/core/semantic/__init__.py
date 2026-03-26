"""
Semantic Context Engine Package

Provides NLP-based context understanding for risk assessment.
Uses embedding-based models to analyze market context and sentiment.
Includes ML scaffold for future model integration.
"""

from .context_engine import (
    SemanticContextEngine,
    ContextConfig,
    ContextInput,
    ContextResult,
    VolatilityRegime,
    SpreadRegime,
    SessionContext,
    # ML Scaffold
    MLFeatureVector,
    MLPrediction,
    SemanticContextInterface,
)

__all__ = [
    "SemanticContextEngine",
    "ContextConfig",
    "ContextInput",
    "ContextResult",
    "VolatilityRegime",
    "SpreadRegime",
    "SessionContext",
    # ML Scaffold
    "MLFeatureVector",
    "MLPrediction", 
    "SemanticContextInterface",
]

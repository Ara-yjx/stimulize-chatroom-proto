declare const Qualtrics: {
  SurveyEngine?: {
    setJSEmbeddedData?: (key: string, value: string) => void;
    setEmbeddedData?: (key: string, value: string) => void;
    hideNextButton?: () => void;
    showNextButton?: () => void;
  };
} | undefined;

# RQ1 transformations: format round-trips

1551 unique models; UVL -> format -> flamapy reader, compared on features, number of constraints and logical equivalence (SAT: each model entails every CNF clause of the other).

| Format | ok | differs | unsupported | write_error | read_error | analysis_error | model_error |
|---|---|---|---|---|---|---|---|
| uvl | 1544 | 0 | 0 | 0 | 0 | 0 | 7 |
| featureide | 1244 | 13 | 287 | 0 | 0 | 0 | 7 |
| glencoe | 1255 | 13 | 276 | 0 | 0 | 0 | 7 |
| afm | 12 | 8 | 0 | 504 | 1020 | 0 | 7 |
| json | 1531 | 13 | 0 | 0 | 0 | 0 | 7 |
| fama_xml | 1319 | 215 | 0 | 0 | 10 | 0 | 7 |
| featureide+refactorings | 1520 | 14 | 10 | 0 | 0 | 0 | 7 |
| glencoe+refactorings | 1531 | 13 | 0 | 0 | 0 | 0 | 7 |

## Most frequent non-ok details

- afm / differs: 1 x `features (+14/-112)`
- afm / differs: 1 x `features (+5/-4)`
- afm / differs: 1 x `features (+13/-8)`
- afm / differs: 1 x `features (+5/-3)`
- afm / read_error: 996 x `IndexError`
- afm / read_error: 16 x `FlamaException`
- afm / read_error: 8 x `AttributeError`
- afm / write_error: 457 x `TypeError`
- afm / write_error: 47 x `FlamaException`
- fama_xml / differs: 3 x `constraints 886->687; configurations`
- fama_xml / differs: 3 x `constraints 882->671; configurations`
- fama_xml / differs: 3 x `constraints 816->635; configurations`
- fama_xml / differs: 3 x `constraints 4->1; configurations`
- fama_xml / read_error: 10 x `DuplicatedFeature`
- featureide / differs: 3 x `constraints 1->0`
- featureide / differs: 3 x `constraints 2->0`
- featureide / differs: 1 x `constraints 93->59`
- featureide / differs: 1 x `constraints 7->0`
- featureide / unsupported: 287 x `FlamaException`
- featureide+refactorings / differs: 3 x `constraints 1->0`
- featureide+refactorings / differs: 3 x `constraints 2->0`
- featureide+refactorings / differs: 1 x `constraints 93->59`
- featureide+refactorings / differs: 1 x `constraints 7->0`
- featureide+refactorings / unsupported: 10 x `FlamaException`
- glencoe / differs: 3 x `constraints 1->0`
- glencoe / differs: 3 x `constraints 2->0`
- glencoe / differs: 1 x `constraints 93->59`
- glencoe / differs: 1 x `constraints 7->0`
- glencoe / unsupported: 276 x `FlamaException`
- glencoe+refactorings / differs: 3 x `constraints 1->0`
- glencoe+refactorings / differs: 3 x `constraints 2->0`
- glencoe+refactorings / differs: 1 x `constraints 93->59`
- glencoe+refactorings / differs: 1 x `constraints 7->0`
- json / differs: 3 x `constraints 1->0`
- json / differs: 3 x `constraints 2->0`
- json / differs: 1 x `constraints 93->59`
- json / differs: 1 x `constraints 7->0`

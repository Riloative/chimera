# English → Lithuanian glossary

Register: the polite plural (**jūs**) in all instructions and system messages
("Pasirinkite…", "Bandykite dar kartą"). Keep wording short and neutral.
Typography: Lithuanian quotation marks „…“, en dash with spaces ( – ) for
breaks, `el. paštas` abbreviation.

| English | Lithuanian | Notes |
| --- | --- | --- |
| chat, conversation | pokalbis | "New chat" → *Naujas pokalbis* |
| message | žinutė | |
| settings | nustatymai | |
| model | modelis | |
| prompt (reusable instruction) | užklausa | library → *užklausų biblioteka* |
| system prompt | sistemos užklausa | |
| knowledge base | žinių bazė | nav item "Knowledge" → *Žinios* |
| collection | rinkinys | *žinių rinkinys* |
| document | dokumentas | |
| chunk | fragmentas | |
| excerpt (retrieved passage) | ištrauka | |
| attachment | priedas | |
| connection (to a server) | ryšys | plural *ryšiai* |
| shared connection | bendras ryšys | |
| sign in | prisijungti | |
| sign out | atsijungti | |
| register / create account | registruotis / sukurti paskyrą | |
| account | paskyra | |
| password | slaptažodis | |
| login name | prisijungimo vardas | |
| email | el. paštas | |
| administrator | administratorius | |
| user | naudotojas | (not *vartotojas*) |
| session | seansas | |
| token (LLM) | žetonas | "tok/s" → *žet./s* |
| regenerate | generuoti iš naujo | |
| stop | sustabdyti | |
| embeddings | įterpiniai | |
| reasoning | samprotavimas | |
| inference | išvados | |
| endpoint | galinis taškas | |
| slot (llama-server) | lizdas | |
| seed | sėkla | |
| timeout | laukimo laikas | |
| vision (image input) | vaizdai / vaizdų atpažinimas | |
| web search | paieška internete | toggle label: *Paieška* |
| search engine | paieškos sistema | |
| search query | paieškos užklausa | (see *užklausa* for prompt below) |
| source | šaltinis | plural *šaltiniai*, gen. pl. *šaltinių* |
| citation | nuoroda į šaltinį | aria-label "{n} šaltinis: …" |
| link (URL) | nuoroda | |
| read a page / read again | perskaityti puslapį / perskaityti iš naujo | "Reading 4 sources" → *Skaitoma 4 šaltinių*… with plural forms |
| page | puslapis | |
| search result | paieškos rezultatas | |
| snippet / summary | santrauka | "Summary only" → *Tik santrauka* |
| context window | konteksto langas | "context limit" → *konteksto riba* |
| web access | prieiga prie interneto | Settings section "Web" → *Internetas* |
| allow-list / deny-list | leidžiamų sąrašas / „niekada neskaityti“ | phrased as full sentences in the UI |
| user agent | naudotojo agentas | |
| port | prievadas | |
| robots.txt | robots.txt | not translated |
| branch (of a conversation) | šaka | "this and all its branches" → *visose šakose* |
| version (of a message) | versija | navigator: *1 versija iš 2*, *Atsakymas 1 iš 2* |
| pin to context | prisegti prie konteksto | badge *Prisegta* |
| exchange (user message + answer) | apsikeitimas | |
| leave out (history) | praleisti | marker: *neįtraukta į modelio kontekstą* |
| summarize / summary | apibendrinti / santrauka | |
| tokenizer | žetonų skaidytuvas | |
| draft | juodraštis | |
| headless browser | foninė naršyklė | literally "background browser" |
| render (a page) | atvaizduoti | "Render with browser" → *Atvaizduoti naršykle* |

## Phrases that deserve native-speaker review

These were the least certain choices:

- **"užklausa" for prompt** – also means "query/request"; *raginimas* or the
  loanword *promptas* are alternatives.
- **"ryšys" for connection** – *jungtis* is a plausible alternative.
- **"žetonas" / "žet./s" for LLM tokens** – technical writers sometimes keep
  *tokenas*.
- **"įterpiniai" for embeddings**, **"išvados" for inference**,
  **"lizdai" for slots**, **"sėkla" for seed** – established in academic usage
  but may read as unusual to users.
- **Sampling parameter names** ("Bauda už kartojimą", "Bauda už buvimą",
  "Bauda už dažnumą", "Branduolio atrankos tikimybių suma").
- **Greetings** "Laba diena" (afternoon) and "Dirbate vėlai" (late night).

Web reading and search (0.3), additionally:

- **"užklausa"** now appears both as *prompt* (library) and in *paieškos
  užklausa* (search query); a reviewer may prefer *raginimas* for prompts to
  keep them apart.
- **"nuoroda"** is used for a link and in *nuoroda į šaltinį* (citation).
- **Plural sentences with two numbers**, e.g. *"Dėl modelio konteksto ribos
  panaudota 2 iš 3 šaltinių"* (the plural form follows the second number), and
  *"Nepavyko perskaityti 1 šaltinio – panaudota paieškos santrauka"*.
- **"Bus perskaityta ne daugiau kaip 5 nuorodų"** (limit hint) and
  **"Skaitoma 5 šaltinių"** (impersonal *skaitoma* with the genitive plural).
- **Technical terms:** *naudotojo agentas* (user agent), *prievadas* (port),
  *vietos rezervavimo ženklai* (placeholders), *nuorašai* (transcripts),
  *debesijos metaduomenų adresai* (cloud metadata addresses).
- **Privacy notice wording** (Settings → Web, first use of search): accuracy
  matters more than style here.
- **"many" plural forms** (e.g. "1,5 pokalbio") – only reachable with fractional
  counts, which the UI never produces, but provided for completeness.
- **Mixed constructions with interpolated names**, e.g. "Ryšys „{{name}}“
  išjungtas", "{{name}} nepasiekiamas" – grammatical gender/case of the
  interpolated name can't be inflected automatically.

Branches, context management and the headless browser (0.4), additionally:

- **"foninė naršyklė" for headless browser** – a descriptive choice;
  *begalvė naršyklė* (literal) or *naršyklė be sąsajos* are alternatives, and
  admins may know the English term better.
- **"atvaizduoti" for render** ("Atvaizduoti naršykle", "Atvaizduota foninės
  naršyklės") – *pateikti* or *įkelti* are possible; check the instrumental
  *naršykle* reads naturally on a button.
- **"šaka" for branch** and **"versija"** in *"1 versija iš 2"* (word order
  with the numeral first).
- **"apsikeitimas" for an exchange** (a question with its answer) – may read
  as unusual; *klausimas ir atsakymas* is longer but plainer.
- **"prisegti prie konteksto" for pin to context**, distinct from pinning a
  conversation in the sidebar (*prisegti*).
- **"žetonų skaidytuvas" for tokenizer** – *tokenizatorius* is the common
  loanword.
- **Marker sentences with plural agreement**, e.g. *"3 ankstesnės žinutės
  neįtrauktos į modelio kontekstą"*, *"12 ankstesnių žinučių neįtraukta…"*,
  *"Kad tilptų puslapiai, praleista 12 ankstesnių žinučių"*.
- **Delete confirmation** *"Bus pašalintos 3 žinutės: ši ir viskas, kas iš jos
  tęsiasi, visose šakose."* – for counts in the *one* category above 1 (21,
  31…) the shorter form without the explanation is used.
- **Error texts with numbers**: *"…jai reikia maždaug {{needed}} žetonų, o
  laisvų yra tik {{available}}"* – the genitive plural after numbers ending in
  1 (21 žetonas) is not inflected.
- **Admin status lines** for the browser service ("Nesukonfigūruota. Paleiskite
  browser profilį…") mix Lithuanian with untranslated configuration names.

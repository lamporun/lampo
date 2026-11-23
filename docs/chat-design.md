# Interfaccia semplice per chi non è esperto: progetto (23/11/2026)

## Cosa vede l'utente (lampo.run/chat)
1. Una casella in cui scrivere la domanda e un menu con il modello (preimpostato: DeepSeek V4 Flash,
   «1.000 sats ≈ 0,60 $: basta per centinaia di domande»).
2. Alla prima domanda compare un **QR code** con il prezzo e un pulsante «Apri nel wallet».
   Se nel browser c'è un wallet (estensione Alby, WebLN), basta un clic su «Paga».
3. L'utente inquadra il QR con il wallet del telefono e paga. In 2-10 secondi la pagina se ne accorge
   da sola e la risposta compare parola per parola.
4. Le domande successive usano lo stesso pacchetto, senza pagare; un indicatore mostra quanti token
   restano. Finito il pacchetto, compare un nuovo QR.
5. La conversazione resta solo nel browser dell'utente: non la salviamo da nessuna parte.

## Come funziona dietro
- La pagina usa la stessa API di sempre (`/v1/chat/completions`): riceve il 402 con macaroon e fattura.
- Il problema: un wallet sul telefono non restituisce al browser la prova del pagamento (preimage).
  CORREZIONE 23/11: il campo `progress.preimage` esiste in `wcli activity`, ma per le ricezioni
  completate è VUOTO. La soluzione sotto va quindi rifatta: per esempio pagamento via WebLN (wallet nel
  browser, che restituisce la preimage), oppure un'API di Wavelength che esponga la preimage, da cercare.
- Un piccolo servizio nuovo, `/pay/status?hash=…`, in sola lettura e con limite di richieste:
  se la fattura con quell'hash è stata pagata, restituisce la preimage. La pagina la unisce al macaroon
  (che ha solo lei) e la credenziale resta nel browser (localStorage).
  Sicurezza: conoscere solo la fattura non basta, serve anche il macaroon, che sta solo nel browser che
  l'ha ricevuto. Il servizio usa un macaroon del wallet limitato alla lettura (`wcli bakemacaroon`).
- QR generato nella pagina (libreria piccola, ospitata da noi: la CSP permette solo il nostro dominio).
- Tempo stimato: circa un giorno di lavoro, test con un pagamento vero incluso.

## Il punto delicato: legale e fisco
Un'interfaccia «per i meno esperti» porta **consumatori**, non più solo sviluppatori e aziende (B2B).
Con i consumatori cambiano le regole:
- **Identità del venditore:** il Codice del Consumo chiede nome reale o ragione sociale e un indirizzo
  completo. Oggi mostriamo solo «Unnecessary Pixels, Cesano Maderno».
- **Diritto di recesso (14 giorni)** sui servizi digitali: si perde solo se l'utente, prima di pagare,
  chiede espressamente l'esecuzione immediata e lo dichiara con una spunta.
- **IVA:** i servizi digitali venduti a consumatori UE seguono regole IVA proprie (paese del cliente,
  regime OSS). Da verificare con il commercialista, soprattutto senza partita IVA.

## Due strade
- **A. «Playground per sviluppatori» (consigliata ora):** stessa interfaccia, presentata come prova
  dell'API per chi sviluppa, con una spunta obbligatoria «Uso Lampo per lavoro (sviluppatore o azienda)»
  e quella per l'esecuzione immediata. Resta nel perimetro B2B dei termini attuali. Rischio basso,
  non nullo: qualche consumatore può comunque usarla.
- **B. Chat per tutti:** più pubblico, ma prima servono commercialista (IVA, partita IVA), identità
  completa del venditore e termini per i consumatori.

"""Generate fake call-center conversations with emotion labels.

Usage: python scripts/synth_data.py --calls 3000 --seed 42 --out data/synthetic
Writes train/val/test .jsonl, split BY CALL (no leakage between splits).
Real data later: produce the same schema (app/schema.py) and point training at it.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.emotions import EMOTIONS  # noqa: E402
from app.schema import Turn  # noqa: E402

# domain -> (tenant_slug, {lang: [issue phrases]})
DOMAINS = {
    "delivery": ("acme-delivery", {
        "en": ["my parcel", "my package", "the delivery", "my order"],
        "fr": ["mon colis", "ma livraison", "ma commande"],
        "ar": ["الطرد", "الشحنة", "طلبي"]}),
    "isp": ("swiftnet-isp", {
        "en": ["my internet", "my connection", "my wifi", "my bill"],
        "fr": ["ma connexion", "mon internet", "ma facture"],
        "ar": ["الإنترنت", "الاتصال", "الفاتورة"]}),
    "plants": ("maisonverte", {
        "en": ["my plant order", "the monstera", "my delivery"],
        "fr": ["ma plante", "mon monstera", "ma commande"],
        "ar": ["النبتة", "طلبي", "التوصيل"]}),
}
LOCALE = {"en": "en-US", "fr": "fr-FR", "ar": "ar"}

# lang -> emotion -> templates. {x} = the issue phrase.
T = {
    "en": {
        "neutral": ["Hello, I'm calling about {x}.", "I'd like some information on {x}.",
                    "Can you check {x} for me?", "Yes, it's under my name.",
                    "My number is the one I'm calling from."],
        "satisfied": ["Great, thank you so much, that solved {x}!", "Perfect, that's exactly what I needed.",
                      "Thanks, you've been really helpful.", "Wonderful, I'm happy with that."],
        "confused": ["Sorry, I don't understand what happened with {x}.", "Wait, what do you mean exactly?",
                     "I'm not sure I follow, can you explain {x} again?", "Hmm, which option should I choose?"],
        "frustrated": ["I already told you my number, why do I have to repeat it?",
                       "This is the third time I'm calling about {x}.", "Again? Nobody can fix {x}!",
                       "I've been waiting forever and nothing changes."],
        "angry": ["This is unacceptable! {x} is a complete disaster!",
                  "I'm furious, I want to speak to your manager now!",
                  "You people are useless, fix {x} right now!", "I'm cancelling everything, this is ridiculous!"],
        "anxious": ["I'm really worried, I need {x} urgently.",
                    "Please hurry, it's very important and I'm afraid it's lost.",
                    "What if {x} doesn't arrive in time?", "I'm stressed, can you promise it will be fixed?"],
        "sad": ["That's so disappointing, I really counted on {x}.", "I feel let down, it was a gift for someone.",
                "Oh no... that's really sad news about {x}.", "I'm just tired of all this, honestly."]},
    "fr": {
        "neutral": ["Bonjour, j'appelle au sujet de {x}.", "Je voudrais des informations sur {x}.",
                    "Pouvez-vous vérifier {x} s'il vous plaît ?", "Oui, c'est à mon nom.",
                    "Mon numéro est celui d'où j'appelle."],
        "satisfied": ["Super, merci beaucoup, {x} est réglé !", "Parfait, c'est exactement ce qu'il me fallait.",
                      "Merci, vous avez été très aimable.", "Génial, je suis content de cette solution."],
        "confused": ["Excusez-moi, je ne comprends pas ce qui s'est passé avec {x}.",
                     "Attendez, que voulez-vous dire exactement ?",
                     "Je ne suis pas sûr de suivre, pouvez-vous réexpliquer {x} ?",
                     "Hmm, quelle option dois-je choisir ?"],
        "frustrated": ["Je vous ai déjà donné mon numéro, pourquoi le répéter ?",
                       "C'est la troisième fois que j'appelle pour {x}.", "Encore ? Personne ne règle {x} !",
                       "J'attends depuis une éternité et rien ne change."],
        "angry": ["C'est inadmissible ! {x} est une catastrophe !",
                  "Je suis furieux, je veux parler à votre responsable maintenant !",
                  "Vous êtes nuls, réglez {x} tout de suite !", "J'annule tout, c'est ridicule !"],
        "anxious": ["Je suis très inquiet, j'ai besoin de {x} d'urgence.",
                    "Dépêchez-vous s'il vous plaît, c'est important, j'ai peur que ce soit perdu.",
                    "Et si {x} n'arrive pas à temps ?",
                    "Je suis stressé, pouvez-vous me promettre que ce sera réglé ?"],
        "sad": ["C'est tellement décevant, je comptais vraiment sur {x}.", "Je me sens déçu, c'était un cadeau.",
                "Oh non... c'est triste, pour {x}.", "J'en ai juste marre de tout ça, honnêtement."]},
    "ar": {
        "neutral": ["مرحبا، أتصل بخصوص {x}.", "أريد بعض المعلومات عن {x}.",
                    "هل يمكنك التحقق من {x} من فضلك؟", "نعم، هو باسمي.", "رقمي هو الرقم الذي أتصل منه."],
        "satisfied": ["ممتاز، شكرا جزيلا، تم حل {x}!", "رائع، هذا بالضبط ما أحتاجه.",
                      "شكرا لك، كنت مفيدا جدا.", "جميل، أنا راض عن هذا الحل."],
        "confused": ["عفوا، لا أفهم ماذا حدث مع {x}.", "انتظر، ماذا تقصد بالضبط؟",
                     "لست متأكدا أنني فهمت، هل تشرح {x} مرة أخرى؟", "همم، أي خيار أختار؟"],
        "frustrated": ["لقد أعطيتك رقمي من قبل، لماذا أكرره؟", "هذه ثالث مرة أتصل فيها بخصوص {x}.",
                       "مرة أخرى؟ لا أحد يصلح {x}!", "أنتظر منذ زمن طويل ولا شيء يتغير."],
        "angry": ["هذا غير مقبول! {x} كارثة حقيقية!", "أنا غاضب جدا، أريد التحدث إلى المسؤول الآن!",
                  "أنتم عديمو الفائدة، أصلحوا {x} حالا!", "سألغي كل شيء، هذا سخيف!"],
        "anxious": ["أنا قلق جدا، أحتاج {x} بشكل عاجل.", "من فضلك أسرع، الأمر مهم وأخاف أنه ضاع.",
                    "ماذا لو لم يصل {x} في الوقت المناسب؟", "أنا متوتر، هل تعدني أنه سيُحل؟"],
        "sad": ["هذا مخيب للآمال، كنت أعتمد على {x}.", "أشعر بخيبة أمل، كانت هدية.",
                "يا للأسف... خبر محزن عن {x}.", "لقد تعبت من كل هذا، بصراحة."]},
}

from templates_extra import EXTRA  # noqa: E402

for _lang, _emos in EXTRA.items():
    for _emo, _texts in _emos.items():
        T[_lang][_emo] = T[_lang][_emo] + _texts

# Typical emotional arcs of a call (start -> ... -> end).
ARCS = [
    ["neutral", "neutral", "satisfied"],
    ["neutral", "confused", "neutral", "satisfied"],
    ["neutral", "frustrated", "angry"],
    ["neutral", "frustrated", "frustrated", "angry", "angry"],
    ["anxious", "anxious", "neutral", "satisfied"],
    ["neutral", "sad", "sad"],
    ["confused", "confused", "frustrated", "neutral"],
    ["angry", "frustrated", "neutral", "satisfied"],
    ["neutral", "anxious", "sad"],
]


def tweak(text, rng):
    """Light noise so the model doesn't just memorise templates."""
    r = rng.random()
    if r < 0.15:
        text = text.lower()
    elif r < 0.25:
        text = text.rstrip(".!?")
    if rng.random() < 0.15:
        text = rng.choice(["Euh... ", "Um, ", "Well, ", "Alors, ", "يعني، "]) + text
    return text


def make_call(i, rng):
    lang = rng.choice(["en", "fr", "ar"])
    slug, issues = DOMAINS[rng.choice(list(DOMAINS))]
    fails = 0
    turns = []
    for k, emo in enumerate(rng.choice(ARCS)):
        text = tweak(rng.choice(T[lang][emo]).format(x=rng.choice(issues[lang])), rng)
        if emo in ("frustrated", "angry", "confused") and rng.random() < 0.6:
            fails += 1
        barge = (emo == "angry" and rng.random() < 0.5) or rng.random() < 0.03
        turns.append(Turn(f"call-{i:06d}", slug, LOCALE[lang], k, text, fails, barge, emo))
    return turns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/synthetic")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    calls = [make_call(i, rng) for i in range(a.calls)]
    rng.shuffle(calls)
    n = len(calls)
    cut = {"train": calls[:int(.8 * n)], "val": calls[int(.8 * n):int(.9 * n)], "test": calls[int(.9 * n):]}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, cs in cut.items():
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for c in cs:
                for t in c:
                    d = t.to_dict()
                    # 4% label noise like real annotators, but ONLY in train:
                    # val/test stay clean so the score is honest.
                    if name == "train" and rng.random() < 0.04:
                        d["emotion"] = rng.choice(EMOTIONS)
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(name, len(cs), "calls")


if __name__ == "__main__":
    main()

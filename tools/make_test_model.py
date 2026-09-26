"""Erzeugt ein winziges, zufällig initialisiertes BERT-NER-Modell – nur für automatische Tests
der ONNX-Kette (Export, Quantisierung, Tokenizer-Offsets, Aggregation). Kein Download nötig.

    python tools/make_test_model.py <zielordner>
"""
import sys
from pathlib import Path

from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors, trainers
from transformers import BertConfig, BertForTokenClassification, PreTrainedTokenizerFast

TEXT = [
    "Sehr geehrte Frau Anna Schneider, vielen Dank für Ihr Schreiben aus Hamburg.",
    "Maximilian Bergmann arbeitet seit 2019 bei der Nordlicht Software GmbH in Berlin.",
    "Die Vertretung übernimmt Dr. Ayşe Yıldırım-Schäfer vom Klinikum Nord in Köln.",
] * 20


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tok = Tokenizer(models.WordPiece(unk_token="[UNK]"))
    tok.normalizer = normalizers.NFC()
    tok.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tok.train_from_iterator(TEXT, trainers.WordPieceTrainer(
        vocab_size=300, special_tokens=["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]))
    cls_id, sep_id = tok.token_to_id("[CLS]"), tok.token_to_id("[SEP]")
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", pair="[CLS] $A [SEP] $B [SEP]",
        special_tokens=[("[CLS]", cls_id), ("[SEP]", sep_id)])
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, unk_token="[UNK]", pad_token="[PAD]",
                                   cls_token="[CLS]", sep_token="[SEP]", mask_token="[MASK]",
                                   model_max_length=128)
    fast.save_pretrained(out)
    labels = ["O", "B-PER", "I-PER", "B-LOC", "I-LOC", "B-ORG", "I-ORG", "B-MISC", "I-MISC"]
    cfg = BertConfig(vocab_size=tok.get_vocab_size(), hidden_size=32, num_hidden_layers=2,
                     num_attention_heads=2, intermediate_size=64, max_position_embeddings=128,
                     id2label=dict(enumerate(labels)), label2id={l: i for i, l in enumerate(labels)})
    BertForTokenClassification(cfg).save_pretrained(out)
    print("ok", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))

'''
使用BERT, 30000条
F值最高78.9%
缺陷：无法进行实体重叠的关系抽取
'''
#! -*- coding:utf-8 -*-
import codecs
import os
import numpy as np
import tensorflow as tf
from data_process import id2predicate, list_find, predicate2id
import json
from transformers import BertTokenizer, TFBertModel
from tqdm import tqdm


import logging
logging.disable(30)

os.environ["CUDA_VISIBLE_DEVICES"] = "0"

train_data = json.load(open('./data_trans/train_data_me.json', encoding='utf-8'))
dev_data = json.load(open('./data_trans/dev_data_me.json', encoding='utf-8'))

# bert配置
checkpoint_path = "./bert_model/chinese_L-12_H-768_A-12/"
tokenizer = BertTokenizer.from_pretrained(checkpoint_path, lowercase=True, add_special_tokens=True)
bert_model = TFBertModel.from_pretrained(checkpoint_path)

num_class = 50
label_class = 3
lr = 2e-5
epsilon = 1e-06
num_epochs = 20
batch_size = 6
dropout = 0.5
'''
ner:预测subject/object
perdicate:预测头部关系矩阵(128*128)
'''
def get_input_bert(data):
    input_x, input_segment, input_mask, input_ner, input_re = [], [], [], [], []
    for l in tqdm(range(30000)):
        items = {}
        line = data[l]
        text = line['text'][0:126]
        word_list = [key for key in text]
        word_list.insert(0, "[CLS]")
        word_list.append("[SEP]")
        spo = line['spo_list']
        #token_ids = tokenizer.encode(text, max_length=128)
        token_ids = tokenizer.convert_tokens_to_ids(word_list)
        segment_ids = np.zeros(len(token_ids))
        mask = np.ones(len(token_ids))
        for sp in spo:
            sp = (tokenizer.convert_tokens_to_ids([key for key in sp[0]]), sp[1], tokenizer.convert_tokens_to_ids([key for key in sp[2]]))
            subjectid = list_find(token_ids, sp[0])
            objectid = list_find(token_ids, sp[2])
            if subjectid != -1 and objectid != -1:
                key = (subjectid, subjectid + len(sp[0]))
                if key not in items:
                    items[key] = []
                items[key].append((objectid,
                                   objectid + len(sp[2]),
                                   predicate2id[sp[1]] + 1))
        if items:
            input_x.append(token_ids)
            input_segment.append(segment_ids)
            input_mask.append(mask)
            #seq_len.append(len(text2id))
            ner_s = np.zeros(len(token_ids), dtype=np.int32)
            er_s = np.zeros((128, 128), dtype=np.int32)
            for j in items:
                ner_s[j[0]] = 1
                ner_s[j[0]+1:j[1]] = 2
                for k in items[j]:
                    ner_s[k[0]] = 1
                    ner_s[k[0]+1:k[1]] = 2
                    er_s[j[0]][k[0]] = k[2]
            #print(ner_s)
            input_ner.append(ner_s)
            input_re.append(er_s)

    #seq_len = np.array(seq_len, dtype=np.int32)
    input_re = np.array(input_re, dtype=np.int32)
    input_x = tf.keras.preprocessing.sequence.pad_sequences(input_x, 128, padding='post', truncating='post')
    input_segment = tf.keras.preprocessing.sequence.pad_sequences(input_segment, 128, padding='post', truncating='post')
    input_mask = tf.keras.preprocessing.sequence.pad_sequences(input_mask, 128, padding='post', truncating='post')
    input_ner = tf.keras.preprocessing.sequence.pad_sequences(input_ner, 128, padding='post', truncating='post')
    return input_x, input_segment, input_mask, input_ner, input_re

# input_x, input_segment, input_ner, input_re = get_input_bert(train_data)
# print(train_data[0])
# print(input_x[0])
# print(input_segment[0])
# print(input_ner[0])
# print(input_re[0][21])

class data_loader():
    def __init__(self):
        self.input_x, self.input_segment,self.input_mask, self.input_ner, self.input_re = get_input_bert(train_data)
        self.input_x = self.input_x.astype(np.int32)
        self.input_segment = self.input_segment.astype(np.int32)
        self.input_mask = self.input_mask.astype(np.int32)
        self.input_ner = self.input_ner.astype(np.int32)
        self.input_re = self.input_re.astype(np.int32)
        self.num_train = self.input_x.shape[0]
        self.db_train = tf.data.Dataset.from_tensor_slices((self.input_x, self.input_segment, self.input_mask, self.input_ner, self.input_re))
        self.db_train = self.db_train.shuffle(self.num_train).batch(batch_size, drop_remainder=True)

    def get_batch(self, batch_s):
        indics = np.random.randint(0, self.num_train, batch_s)
        return self.input_x[indics], self.input_segment[indics], self.input_mask[indics], self.input_ner[indics], self.input_re[indics]

'''
epoch20, 最大F=81.1
'''
class Ner_model(tf.keras.Model):
    def __init__(self, bert_model):
        super(Ner_model, self).__init__()
        self.bert = bert_model
        #self.dense_fuc = tf.keras.layers.Dense(100, use_bias=False) #全连接层
        self.dense = tf.keras.layers.Dense(label_class)

    def call(self, inputs, mask, segment):
        output_encode, _ = self.bert([inputs, mask, segment])
        #x = self.dense_fuc(output_encode)
        x = self.dense(output_encode)
        x = tf.nn.softmax(x)
        return x, output_encode

class ER_model(tf.keras.Model):
    def __init__(self):
        super(ER_model, self).__init__()
        self.label_embedding = tf.keras.layers.Embedding(3, 256)
        self.dense_label = tf.keras.layers.Dense(128, use_bias=True)
        self.dense_left = tf.keras.layers.Dense(128, use_bias=False)
        self.dense_right = tf.keras.layers.Dense(128, use_bias=False)
        self.dropout = tf.keras.layers.Dropout(dropout)
        self.dense = tf.keras.layers.Dense(num_class)

    def call(self, encode_input, ner):
        label_embedding = self.label_embedding(ner)
        label_embedding = self.dense_label(label_embedding)
        encode_input = tf.concat([encode_input, label_embedding], axis=-1)
        left = self.dense_left(encode_input)
        right = self.dense_right(encode_input)
        outer_sum = broadcasting(left, right)
        output = tf.tanh(outer_sum)
        output = self.dropout(output)
        output = self.dense(output)
        output = tf.sigmoid(output)
        return output

def broadcasting(left, right):
    left = tf.transpose(left, perm=[1, 0, 2])
    left = tf.expand_dims(left, 3)
    right = tf.transpose(right, perm=[0, 2, 1])
    right = tf.expand_dims(right, 0)
    B = left + right
    B = tf.transpose(B, perm=[1, 0, 3, 2])
    return B

def loss_function(ner, re_pred, input_nerd, input_red):
    ner_one_hot = tf.one_hot(input_nerd, depth=3, dtype=tf.float32)
    loss_ner = tf.keras.losses.categorical_crossentropy(y_true=ner_one_hot, y_pred=ner)
    loss_ner = tf.reduce_sum(loss_ner)

    input_re_onehot = tf.one_hot(input_red, depth=num_class, dtype=tf.float32)
    loss_re = tf.keras.losses.binary_crossentropy(y_true=input_re_onehot, y_pred=re_pred)
    loss_re = tf.reduce_sum(loss_re)

    loss = (loss_ner + loss_re)
    return loss, loss_ner, loss_re


class Extra_result(object):
    def __init__(self, text, spo_list):
        self.text = text
        self.spo = spo_list
    def call(self):
        result = []
        word_list = [key for key in self.text]
        word_list.insert(0, "[CLS]")
        word_list.append("[SEP]")
        segment_ids = np.zeros(len(word_list))
        mask = np.ones(len(word_list))
        token = tf.constant(tokenizer.convert_tokens_to_ids(word_list), dtype=tf.int32)[None, :]
        segment_ids = tf.constant(segment_ids, dtype=tf.int32)[None, :]
        mask = tf.constant(mask, dtype=tf.int32)[None, :]
        Model_ner = model_Ner
        ner, encode = Model_ner(token, mask, segment_ids)
        subjects, new_ner = self.extra_sujects(ner)
        new_ner = tf.constant(tf.convert_to_tensor(new_ner), dtype=tf.int32)[None, :]
        Model_er = model_Er
        re = Model_er(encode, new_ner)
        relationship = self.extra_er(subjects, re)
        print(subjects)
        print(relationship)
        result.extend(relationship)
        return result

    def extra_sujects(self, ner_label):
        ner = ner_label[0]
        ner = tf.round(ner)
        ner = [tf.argmax(ner[k]) for k in range(ner.shape[0])]
        new_ner = list(np.array(ner))
        ner = list(np.array(ner))[1:-1]
        ner.append(0)#防止最后一位不为0
        text_list = [key for key in self.text]
        subject = []
        for i, k in enumerate(text_list):
            if int(ner[i]) == 0 or int(ner[i]) == 2:
                continue
            elif int(ner[i]) == 1:
                ner_back = [int(j) for j in ner[i + 1:]]
                if 1 in ner_back and 0 in ner_back:
                    indics_1 = ner_back.index(1) + i
                    indics_0 = ner_back.index(0) + i
                    subject.append((''.join(text_list[i: min(indics_0, indics_1) + 1]), i + 1))
                elif 1 not in ner_back:
                    indics = ner_back.index(0) + i
                    subject.append((''.join(text_list[i:indics + 1]), i + 1))
        return subject, new_ner

    def extra_er(self, subjects, re):
        position = [key[1] for key in subjects]
        subjects_ = [key[0] for key in subjects]
        re = re[0]
        relationship = []
        re = tf.argmax(re, axis=-1)
        length = re.shape[0]
        for k in range(length):
            for i, key in enumerate(list(np.array(re[k]))):
                if int(key) > 0:
                    if k in position and i in position:
                        subject = subjects_[position.index(k)]
                        object = subjects_[position.index(i)]
                        predicate = id2predicate[key - 1]
                        relationship.append((subject, predicate, object))
        return relationship


class Evaluate(object):
    def __init__(self):
        pass
    def reset(self,spo_list):
        xx = []
        for key in spo_list:
            xx.append((key[0], key[1], key[2]))
        return xx
    def evaluate(self, data):
        A, B, C = 1e-10, 1e-10, 1e-10
        for d in data[0:10]:
            extra_items = Extra_result(d['text'], self.reset(d['spo_list']))
            R = set(extra_items.call())
            T = set(self.reset(d['spo_list']))
            A += len(R & T)#抽取正确数量
            B += len(R) #抽取数量
            C += len(T)#原正确数量
        return (2 * A / (B + C)), (A / B), (A / C)

#建立模型

model_Ner = Ner_model(bert_model)
model_Er = ER_model()
optimizer = tf.keras.optimizers.Adam(learning_rate=lr)

#保存模型
# checkpoint_dir = './save/Entity_Relationshaip_version2_checkpoints'
# checkpoint_prefix = os.path.join(checkpoint_dir, 'ckpt')
checkpoint = tf.train.Checkpoint(optimizer=optimizer, model_Ner=model_Ner, model_Er=model_Er)

evaluate = Evaluate()
data_loader = data_loader()
best = 0.0

#训练模型
for epoch in range(num_epochs):
    print('Epoch:', epoch + 1)

    num_batchs = int(data_loader.num_train / batch_size) + 1
    for batch_index in range(num_batchs):
        input_x, input_segment, input_mask, input_ner, input_re = data_loader.get_batch(batch_size)
        with tf.GradientTape() as tape:
            y_ner, encode_output = model_Ner(input_x, input_mask, input_segment) #预测ner
            y_re = model_Er(encode_output, input_ner) #预测关系
            loss, loss1, loss2 = loss_function(y_ner, y_re, input_ner, input_re)
            if (batch_index+1) % 100 == 0:
                print("batch %d: loss %f: loss1 %f: loss2 %f" % (batch_index+1, loss.numpy(), loss1.numpy(), loss2.numpy()))

        variables = (model_Ner.variables + model_Er.variables)
        grads = tape.gradient(loss, variables)
        optimizer.apply_gradients(grads_and_vars=zip(grads, variables))

    #f, p, r = evaluate.evaluate(train_data)
    F, P, R = evaluate.evaluate(dev_data)
    #print('训练集:', "f %f: p %f: r %f: " % (f, p, r))
    print('测试集:', "F %f: P %f: R %f: " % (F, P, F))
    if round(F, 2) > best and round(F, 2) > 0.50:
        best = F
        print('saving_model')
        #model.save('./save/Entity_Relationshaip_version2.h5')
        checkpoint.save('./save/Entity_Relationship/version7_checkpoints.ckpt')

